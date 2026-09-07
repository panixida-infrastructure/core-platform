import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "kubernetes/charts/"
          "core-platform-workloads/files/timeweb_control_plane_logs.py")
SPEC = importlib.util.spec_from_file_location("collector", SOURCE)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
NODE = {"id": 1, "node_ip": "192.0.2.1"}
NOW = dt.datetime(2026, 9, 7, 18, 0, tzinfo=dt.timezone.utc).timestamp()


def line(seconds=0, message="test"):
    timestamp = dt.datetime.fromtimestamp(NOW + seconds, dt.timezone.utc).isoformat()
    return f'time="{timestamp}" level=info msg="{message}" component=kube-apiserver'


class FakeClient:
    def __init__(self, pages, fail_export=False, fail_save=False):
        self.pages = pages
        self.exported = []
        self.saved = []
        self.fail_export = fail_export
        self.fail_save = fail_save

    def timeweb(self, path, query):
        assert query["timezone"] == "UTC"
        assert query["scope"] in collector.SCOPES
        index = int(query.get("cursor", "0"))
        return {"k8s_logs": self.pages[index], "meta": {
            "next_cursor": str(index + 1) if index + 1 < len(self.pages) else None}}

    def export(self, node, records):
        if self.fail_export:
            raise OSError("transport failed")
        self.exported.extend(records)

    def save_state(self, state):
        if self.fail_save:
            raise OSError("checkpoint failed")
        self.saved.append(copy.deepcopy(state))


class CollectorTests(unittest.TestCase):
    def test_logrus_glog_error_overrides_outer_info(self):
        component, timestamp, record = collector.normalize(line(message="E0907 18:00:00.123456 error"), NODE, NOW)
        self.assertEqual((component, timestamp, record["severityNumber"]), ("kube-apiserver", NOW, 17))

    def test_provider_klog_timestamp_and_scope(self):
        component, timestamp, record = collector.normalize(
            "E0907 18:00:00 GMT 123456 123 source.go:1] failed", NODE, NOW, "twcp.service")
        self.assertEqual(component, "twcp")
        self.assertAlmostEqual(timestamp, NOW + 0.123456)
        self.assertEqual(record["severityNumber"], 17)

    def test_klog_year_rollover(self):
        january = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        _, timestamp, _ = collector.normalize("I1231 23:59:59 GMT 000000 1 test", NODE, january)
        self.assertEqual(timestamp, january - 1)

    def test_initial_lookback_does_not_replay_old_incidents(self):
        client = FakeClient([[line(-1)], [line(-301)]])
        state = {}
        self.assertEqual(collector.collect_node(client, NODE, state, NOW), 1)
        self.assertTrue(state)

    def test_overlap_handles_ties_reordering_duplicates_and_late_records(self):
        state = {}
        first = FakeClient([[line(0, "a"), line(0, "b"), line(-10, "old")]])
        self.assertEqual(collector.collect_node(first, NODE, state, NOW), 3)
        second = FakeClient([[line(10, "new"), line(0, "b"), line(0, "a")],
                             [line(0, "a"), line(-5, "late"), line(-10, "old")]])
        self.assertEqual(collector.collect_node(second, NODE, state, NOW + 60), 3)
        self.assertCountEqual([r[2]["body"]["stringValue"] for r in second.exported], ["new", "a", "late"])
        unchanged = FakeClient(second.pages)
        self.assertEqual(collector.collect_node(unchanged, NODE, state, NOW + 120), 0)
        self.assertEqual(unchanged.saved, [])

    def test_export_failure_retains_checkpoint(self):
        state = {}
        client = FakeClient([[line()]], fail_export=True)
        with self.assertRaises(OSError):
            collector.collect_node(client, NODE, state, NOW)
        self.assertEqual(state, {})
        self.assertEqual(client.saved, [])

    def test_checkpoint_failure_replays_unconfirmed_delivery(self):
        state = {}
        with self.assertRaises(OSError):
            collector.collect_node(FakeClient([[line()]], fail_save=True), NODE, state, NOW)
        retry = FakeClient([[line()]])
        self.assertEqual(collector.collect_node(retry, NODE, state, NOW), 1)

    def test_empty_window_and_expired_history_do_not_stall(self):
        state = {}
        collector.collect_node(FakeClient([[line()]]), NODE, state, NOW)
        self.assertEqual(collector.collect_node(FakeClient([[]]), NODE, state, NOW + 700000), 0)
        self.assertEqual(collector.collect_node(FakeClient([[line(700000)]]), NODE, state, NOW + 700000), 1)

    def test_page_bound_does_not_advance_or_export_partial_backlog(self):
        state = {}
        client = FakeClient([[line()], [line(-1)]])
        with self.assertRaises(RuntimeError):
            collector.collect_node(client, NODE, state, NOW, page_limit=1)
        self.assertEqual((state, client.exported, client.saved), ({}, [], []))


if __name__ == "__main__":
    unittest.main()
