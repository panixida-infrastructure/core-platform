import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import urllib.error


SOURCE = (Path(__file__).resolve().parents[1] / "kubernetes/charts/"
          "core-platform-workloads/files/timeweb_postgres_backups.py")
SPEC = importlib.util.spec_from_file_location("backups", SOURCE)
backups = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backups)
NOW = dt.datetime(2026, 10, 11, 8, tzinfo=dt.timezone.utc).timestamp()
SETTINGS = {"is_enabled": True, "copy_count": 1, "interval": "day"}


def backup(backup_id=1, kind="auto", status="done", age=3600):
    return {"id": backup_id, "type": kind, "status": status,
            "created_at": dt.datetime.fromtimestamp(NOW - age, dt.timezone.utc).isoformat()}


class SnapshotTests(unittest.TestCase):
    def test_manual_backup_does_not_hide_stale_automatic_backup(self):
        snapshot = backups.summarize(SETTINGS, [backup(age=56 * 3600),
                                              backup(2, "manual", age=3600)], NOW)
        self.assertEqual(NOW - 56 * 3600, snapshot["last_success"]["auto"])
        self.assertEqual(NOW - 3600, snapshot["last_success"]["manual"])

    def test_pending_and_failed_copies_do_not_count_as_success(self):
        snapshot = backups.summarize(SETTINGS, [backup(status="create"),
                                              backup(2, "manual", "fail")], NOW)
        self.assertEqual({"auto": 0, "manual": 0}, snapshot["last_success"])
        self.assertEqual(1, snapshot["counts"]["auto", "create"])
        self.assertEqual(0, snapshot["counts"]["auto", "done"])
        self.assertTrue(snapshot["last_attempt"]["manual"][1])

    def test_later_attempt_replaces_old_failure_regardless_of_inventory_order(self):
        for status in ("done", "create"):
            with self.subTest(status=status):
                snapshot = backups.summarize(SETTINGS, [backup(2, status=status, age=60),
                                                       backup(1, status="fail", age=3600)], NOW)
                self.assertFalse(snapshot["last_attempt"]["auto"][1])

    def test_latest_failure_keeps_previous_success_timestamp(self):
        snapshot = backups.summarize(SETTINGS, [backup(age=7200),
                                              backup(2, status="fail", age=60)], NOW)
        self.assertEqual(NOW - 7200, snapshot["last_success"]["auto"])
        self.assertTrue(snapshot["last_attempt"]["auto"][1])

    def test_empty_inventory_is_a_valid_no_backups_snapshot(self):
        snapshot = backups.summarize(SETTINGS, [], NOW)
        self.assertEqual(0, sum(snapshot["counts"].values()))
        self.assertEqual({"auto": 0, "manual": 0}, snapshot["last_success"])

    def test_disabled_schedule_is_distinct_from_missing_copies(self):
        snapshot = backups.summarize(SETTINGS | {"is_enabled": False}, [backup()], NOW)
        self.assertEqual(0, snapshot["enabled"])
        self.assertEqual(1, snapshot["counts"]["auto", "done"])

    def test_malformed_provider_records_are_not_silently_ignored(self):
        for changes in ({"type": "unknown"}, {"status": "unknown"},
                        {"created_at": "not a date"}, {"created_at": "2026-10-10T00:00:00"},
                        {"created_at": "2030-01-01T00:00:00Z"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                backups.summarize(SETTINGS, [backup() | changes], NOW)

    def test_invalid_settings_are_rejected(self):
        for changes in ({"is_enabled": "false"}, {"copy_count": -1}, {"copy_count": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                backups.summarize(SETTINGS | changes, [], NOW)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = backups.Client(4169562, "unused-token-file")

    def test_pagination_reads_all_pages(self):
        items = [backup(number) for number in range(1, 102)]
        self.client.get = Mock(side_effect=[{"auto_backups_settings": SETTINGS},
                                           {"backups": items[:100], "meta": {"total": 101}},
                                           {"backups": items[100:], "meta": {"total": 101}}])
        with patch.object(backups.time, "time", return_value=NOW):
            self.assertEqual(101, self.client.collect()["counts"]["auto", "done"])
        self.assertEqual("/backups?limit=100&offset=100", self.client.get.call_args.args[0])

    def test_missing_or_inconsistent_pages_fail_closed(self):
        pages = [{"backups": [], "meta": {"total": 1}},
                 {"backups": [backup(), backup()], "meta": {"total": 2}},
                 {"backups": [backup()], "meta": {"total": 0}},
                 {"backups": [], "meta": {"total": True}},
                 {"meta": {"total": 0}}]
        for page in pages:
            with self.subTest(page=page):
                self.client.get = Mock(side_effect=[{"auto_backups_settings": SETTINGS}, page])
                with self.assertRaises((KeyError, ValueError)):
                    self.client.collect()

    def test_inventory_changed_between_pages_is_not_published(self):
        self.client.get = Mock(side_effect=[{"auto_backups_settings": SETTINGS},
            {"backups": [backup(number) for number in range(1, 101)], "meta": {"total": 101}},
            {"backups": [], "meta": {"total": 100}}])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.client.collect()

    def test_pagination_is_bounded(self):
        self.client.get = Mock(side_effect=[{"auto_backups_settings": SETTINGS},
            {"backups": [backup(number) for number in range(1, 101)], "meta": {"total": 101}}])
        with patch.object(backups, "MAX_PAGES", 1), self.assertRaisesRegex(ValueError, "limit"):
            self.client.collect()

    def test_get_retries_transient_errors_and_never_writes(self):
        response = Mock()
        response.__enter__ = Mock(return_value=io.StringIO('{"ok": true}'))
        response.__exit__ = Mock(return_value=False)
        self.client.opener.open = Mock(side_effect=[TimeoutError(), response])
        with patch.object(Path, "read_text", return_value="test-token"), patch.object(backups.time, "sleep"):
            self.assertEqual({"ok": True}, self.client.get("/auto-backups"))
        request = self.client.opener.open.call_args.args[0]
        self.assertEqual("GET", request.get_method())
        self.assertIsNone(request.data)
        self.assertEqual("https://api.timeweb.cloud/api/v1/dbs/4169562/auto-backups", request.full_url)

    def test_unauthorized_is_not_retried(self):
        self.client.opener.open = Mock(side_effect=urllib.error.HTTPError("url", 401, "secret", {}, None))
        with patch.object(Path, "read_text", return_value="test-token"), self.assertRaises(urllib.error.HTTPError):
            self.client.get("/auto-backups")
        self.assertEqual(1, self.client.opener.open.call_count)

    def test_redirects_cannot_forward_provider_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            backups.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")


class MetricsTests(unittest.TestCase):
    def test_initial_failure_does_not_publish_empty_inventory(self):
        metrics = backups.Metrics(4169562, "postgres", 129600)
        client = Mock()
        client.collect.side_effect = RuntimeError("must-not-appear-in-logs")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            metrics.poll(client)
        text = metrics.render()
        self.assertNotIn("must-not-appear-in-logs", output.getvalue())
        self.assertNotIn("timeweb_postgres_backup_count{", text)
        self.assertNotIn("timeweb_postgres_backup_auto_enabled{", text)
        self.assertEqual(0, metrics.last_poll_success)
        self.assertEqual(0, metrics.poll_success)

    def test_failed_poll_preserves_snapshot_but_marks_it_unavailable_then_recovers(self):
        metrics = backups.Metrics(4169562, "postgres", 129600)
        client = Mock()
        client.collect.return_value = backups.summarize(SETTINGS, [backup()], NOW)
        with patch.object(backups.time, "time", return_value=NOW):
            metrics.poll(client)
        previous = copy.deepcopy(metrics.snapshot)
        client.collect.side_effect = ValueError("invalid response")
        with contextlib.redirect_stdout(io.StringIO()):
            metrics.poll(client)
        self.assertEqual(previous, metrics.snapshot)
        self.assertEqual(NOW, metrics.last_poll_success)
        self.assertEqual(0, metrics.poll_success)
        self.assertEqual(1, metrics.errors)
        client.collect.side_effect = None
        with patch.object(backups.time, "time", return_value=NOW + 900):
            metrics.poll(client)
        self.assertEqual(1, metrics.poll_success)
        self.assertEqual(NOW + 900, metrics.last_poll_success)

    def test_metrics_escape_labels_and_do_not_export_backup_ids(self):
        metrics = backups.Metrics(4169562, 'postgres\\"\n', 129600)
        client = Mock()
        client.collect.return_value = backups.summarize(SETTINGS, [backup(987654)], NOW)
        metrics.poll(client)
        text = metrics.render()
        self.assertIn('db_cluster="postgres\\\\\\"\\n"', text)
        self.assertNotIn("987654", text)
        self.assertIn('backup_type="auto",status="done"} 1', text)


if __name__ == "__main__":
    unittest.main()
