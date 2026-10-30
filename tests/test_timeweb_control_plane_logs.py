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
          "core-platform-workloads/files/timeweb_control_plane_logs.py")
SPEC = importlib.util.spec_from_file_location("collector", SOURCE)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
NODE = {"id": 1, "node_ip": "192.0.2.1"}
NOW = dt.datetime(2026, 9, 7, 18, 0, tzinfo=dt.timezone.utc).timestamp()


def line(seconds=0, message="test"):
    timestamp = dt.datetime.fromtimestamp(NOW + seconds, dt.timezone.utc).isoformat()
    return f'time="{timestamp}" level=info msg="{message}" component=kube-apiserver'


def reset_line(peer="176.53.162.227", component="kube-apiserver", message=None):
    message = message or ('E0907 18:00:00.123456 123 upgradeaware.go:428] '
                          '"Error proxying data from client to backend" '
                          f'err="read tcp 192.0.2.1:6443->{peer}:61984: read: connection reset by peer"')
    timestamp = dt.datetime.fromtimestamp(NOW, dt.timezone.utc).isoformat()
    return f'time="{timestamp}" level=info msg={json.dumps(message)} component={component}'


class FakeClient:
    def __init__(self, pages, fail_export=False, fail_save=False):
        self.pages = pages
        self.exported = []
        self.saved = []
        self.fail_export = fail_export
        self.fail_save = fail_save
        self.queries = []

    def timeweb(self, path, query):
        self.queries.append(copy.deepcopy(query))
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
    def test_export_uses_canonical_deployment_environment(self):
        client = collector.Client.__new__(collector.Client)
        client.public = Mock()
        client.request = Mock(return_value={})
        with patch.dict(collector.os.environ, {"OTLP_LOGS_ENDPOINT": "http://collector/v1/logs"}):
            client.export(NODE, [collector.normalize(line(), NODE, NOW)])
        payload = client.request.call_args.args[3]
        resources = payload["resourceLogs"]
        self.assertEqual(len(resources), 1)
        attributes = {a["key"]: a["value"]["stringValue"]
                      for a in resources[0]["resource"]["attributes"]}
        self.assertEqual(attributes["deployment.environment.name"], "production")
        self.assertNotIn("deployment.environment", attributes)

    def test_known_diagnostic_client_reset_is_warning_with_source_error_preserved(self):
        raw = reset_line()
        with patch.object(collector, "DIAGNOSTIC_CLIENT_IPS", {"176.53.162.227"}):
            component, timestamp, record = collector.normalize(raw, NODE, NOW)
        self.assertEqual((component, timestamp, record["severityNumber"], record["severityText"]),
                         ("kube-apiserver", NOW, 13, "Warning"))
        self.assertEqual(record["body"]["stringValue"], json.loads(collector.FIELDS.findall(raw)[2][1]))
        attrs = {attr["key"]: attr["value"]["stringValue"] for attr in record["attributes"]}
        self.assertEqual(attrs["log.original.severity_text"], "Error")
        self.assertEqual(attrs[collector.CLIENT_STREAM_RESET_ATTRIBUTE], "true")

    def test_other_peers_components_scopes_and_transport_failures_remain_errors(self):
        base = json.loads(collector.FIELDS.findall(reset_line())[2][1])
        variants = [
            (reset_line(peer="198.51.100.9"), "k0scontroller.service"),
            (reset_line(component="kube-controller-manager"), "k0scontroller.service"),
            (reset_line(), "twcp.service"),
            (reset_line(message=base.replace("client to backend", "backend to client")), "k0scontroller.service"),
            (reset_line(message=base.replace("read tcp", "write tcp")), "k0scontroller.service"),
            (reset_line(message=base.replace(":6443->", ":43512->")), "k0scontroller.service"),
            (reset_line(message=base.replace("connection reset by peer", "i/o timeout")), "k0scontroller.service"),
            (reset_line(message=base.replace("upgradeaware.go", "other.go")), "k0scontroller.service"),
            (reset_line(message=base + ' error="another failure"'), "k0scontroller.service"),
            (line(message="E0907 18:00:00.123456 OOM failure"), "k0scontroller.service"),
        ]
        with patch.object(collector, "DIAGNOSTIC_CLIENT_IPS", {"176.53.162.227"}):
            for raw, scope in variants:
                with self.subTest(raw=raw, scope=scope):
                    _, _, record = collector.normalize(raw, NODE, NOW, scope)
                    self.assertEqual(record["severityNumber"], 17)
                    self.assertNotIn(collector.CLIENT_STREAM_RESET_ATTRIBUTE,
                                     [attr["key"] for attr in record["attributes"]])

    def test_empty_diagnostic_allowlist_keeps_reset_error(self):
        with patch.object(collector, "DIAGNOSTIC_CLIENT_IPS", set()):
            _, _, record = collector.normalize(reset_line(), NODE, NOW)
        self.assertEqual(record["severityNumber"], 17)

    def test_reset_metric_counts_exported_records_once_across_poll_overlap(self):
        state, metrics = {}, collector.Metrics()
        with patch.object(collector, "DIAGNOSTIC_CLIENT_IPS", {"176.53.162.227"}):
            collector.collect_node(FakeClient([[reset_line(), reset_line(peer="198.51.100.9")]]),
                                   NODE, state, NOW, metrics=metrics)
            collector.collect_node(FakeClient([[reset_line(), reset_line(peer="198.51.100.9")]]),
                                   NODE, state, NOW + 60, metrics=metrics)
        self.assertEqual(metrics.diagnostic_stream_resets, 1)

    def test_failed_export_does_not_increment_reset_metric(self):
        metrics = collector.Metrics()
        with patch.object(collector, "DIAGNOSTIC_CLIENT_IPS", {"176.53.162.227"}):
            with self.assertRaises(OSError):
                collector.collect_node(FakeClient([[reset_line()]], fail_export=True),
                                       NODE, {}, NOW, metrics=metrics)
        self.assertEqual(metrics.diagnostic_stream_resets, 0)

    def test_logrus_glog_error_overrides_outer_info(self):
        component, timestamp, record = collector.normalize(line(message="E0907 18:00:00.123456 error"), NODE, NOW)
        self.assertEqual((component, timestamp, record["severityNumber"]), ("kube-apiserver", NOW, 17))

    def test_provider_klog_timestamp_and_scope(self):
        component, timestamp, record = collector.normalize(
            "E0907 18:00:00 GMT 123456 123 source.go:1] failed", NODE, NOW, "twcp.service")
        self.assertEqual(component, "twcp")
        self.assertAlmostEqual(timestamp, NOW + 0.123456)
        self.assertEqual(record["severityNumber"], 17)

    def test_provider_gmt_offsets_and_native_klog_timestamp(self):
        for clock, suffix in (("18:00:00", " GMT+0000 123456"),
                              ("18:00:00", " GMT+00:00 123456"),
                              ("22:00:00", " GMT+0400 123456"),
                              ("13:00:00", " GMT-0500 123456"),
                              ("18:00:00", ".123456")):
            with self.subTest(suffix=suffix):
                _, timestamp, record = collector.normalize(
                    f"I0907 {clock}{suffix} 123 source.go:1] test", NODE, NOW)
                self.assertAlmostEqual(timestamp, NOW + 0.123456)
                self.assertIn("timeUnixNano", record)

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
        confirmed = copy.deepcopy(state)
        self.assertEqual(collector.collect_node(unchanged, NODE, state, NOW + 120), 0)
        self.assertEqual(state, confirmed)

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

    def test_page_bound_checkpoints_cursor_and_resumes_after_restart(self):
        state = {}
        pages = [[line(0, "head")], [line(-1, "tail")]]
        first = FakeClient(pages)
        self.assertEqual(collector.collect_node(first, NODE, state, NOW, page_limit=1), 1)
        key = "1:k0scontroller.service"
        self.assertNotIn("watermark", state[key])
        self.assertEqual(state[key]["scan"]["cursor"], "1")
        restarted = json.loads(json.dumps(first.saved[-1]))
        second = FakeClient(pages)
        self.assertEqual(collector.collect_node(second, NODE, restarted, NOW + 86400, page_limit=1), 1)
        self.assertEqual(second.queries[0]["cursor"], "1")
        self.assertEqual([r[2]["body"]["stringValue"] for r in second.exported], ["tail"])
        self.assertNotIn("scan", restarted[key])
        self.assertEqual(restarted[key]["watermark"], NOW)

    def test_large_backlog_is_bounded_and_keeps_all_records(self):
        pages = [[line(-p * 500 - i, f"record-{p}-{i}") for i in range(500)] for p in range(24)]
        state = {"1:k0scontroller.service": {"watermark": NOW - 12000, "seen": {}}}
        client = FakeClient(pages)
        metrics = collector.Metrics()
        for _ in range(8):
            collector.collect_node(client, NODE, state, NOW, page_limit=3, metrics=metrics)
            self.assertLess(len(json.dumps(state)), 25000)
            if "scan" in state["1:k0scontroller.service"]:
                self.assertEqual(state["1:k0scontroller.service"]["watermark"], NOW - 12000)
            state = json.loads(json.dumps(client.saved[-1]))
        self.assertEqual(len(client.exported), 12000)
        self.assertEqual(len({r[2]["body"]["stringValue"] for r in client.exported}), 12000)
        self.assertEqual(metrics.records, 12000)
        self.assertEqual(state["1:k0scontroller.service"]["watermark"], NOW)
        self.assertNotIn("scan", state["1:k0scontroller.service"])

    def test_later_page_export_failure_keeps_last_acknowledged_cursor(self):
        client = FakeClient([[line(0, "confirmed")], [line(-1, "retry")]])
        original_export = client.export
        def export(node, records):
            if client.exported:
                raise OSError("private transport failure")
            original_export(node, records)
        client.export = export
        state = {}
        with self.assertRaises(OSError):
            collector.collect_node(client, NODE, state, NOW)
        self.assertEqual(state, client.saved[-1])
        self.assertEqual(state["1:k0scontroller.service"]["scan"]["cursor"], "1")
        retry = FakeClient(client.pages)
        collector.collect_node(retry, NODE, state, NOW + 60)
        self.assertEqual([r[2]["body"]["stringValue"] for r in retry.exported], ["retry"])

    def test_later_page_checkpoint_failure_replays_only_unconfirmed_page(self):
        client = FakeClient([[line(0, "confirmed")], [line(-1, "ambiguous")]])
        original_save = client.save_state
        def save(state):
            if client.saved:
                raise OSError("checkpoint failed")
            original_save(state)
        client.save_state = save
        state = {}
        with self.assertRaises(OSError):
            collector.collect_node(client, NODE, state, NOW)
        self.assertEqual(state, client.saved[-1])
        retry = FakeClient(client.pages)
        collector.collect_node(retry, NODE, state, NOW + 60)
        self.assertEqual([r[2]["body"]["stringValue"] for r in retry.exported], ["ambiguous"])

    def test_records_arriving_during_backfill_are_collected_on_next_scan(self):
        state = {}
        pages = [[line(0, "head")], [line(-1, "tail")]]
        collector.collect_node(FakeClient(pages), NODE, state, NOW, page_limit=1)
        collector.collect_node(FakeClient(pages), NODE, state, NOW + 60, page_limit=1)
        current = FakeClient([[line(60, "new"), line(0, "head"), line(-1, "tail")]])
        self.assertEqual(collector.collect_node(current, NODE, state, NOW + 120), 1)
        self.assertEqual(current.exported[0][2]["body"]["stringValue"], "new")

    def test_overlap_multiplicity_survives_page_boundaries_and_restarts(self):
        state = {}
        collector.collect_node(FakeClient([[line()]]), NODE, state, NOW)
        pages = [[line()], [line()]]
        first, second = FakeClient(pages), FakeClient(pages)
        self.assertEqual(collector.collect_node(first, NODE, state, NOW + 60, page_limit=1), 0)
        state = json.loads(json.dumps(first.saved[-1]))
        self.assertEqual(collector.collect_node(second, NODE, state, NOW + 120, page_limit=1), 1)
        self.assertEqual(collector.collect_node(FakeClient(pages), NODE, state, NOW + 180), 0)

    def test_unknown_timestamp_retains_progress_instead_of_replaying_history_as_new(self):
        state = {}
        client = FakeClient([[line()], ["unrecognized provider timestamp private-content"]])
        with self.assertRaises(collector.CollectionFailure) as failure:
            collector.collect_node(client, NODE, state, NOW)
        self.assertEqual(failure.exception.fields["failure_reason"], "timestamp_missing")
        self.assertEqual(state, client.saved[-1])
        self.assertEqual(state["1:k0scontroller.service"]["scan"]["cursor"], "1")
        self.assertEqual(len(client.exported), 1)

    def test_checkpoint_size_limit_does_not_export_or_advance(self):
        state = {}
        client = FakeClient([[line()]])
        with patch.object(collector, "CHECKPOINT_SIZE_LIMIT", 1):
            with self.assertRaises(collector.CollectionFailure) as failure:
                collector.collect_node(client, NODE, state, NOW)
        self.assertEqual(failure.exception.fields["failure_reason"], "checkpoint_size_limit")
        self.assertEqual((state, client.exported, client.saved), ({}, [], []))

    def test_non_advancing_cursor_keeps_the_last_acknowledged_page(self):
        client = FakeClient([[line()], [line(-1)]])
        original_timeweb = client.timeweb
        def timeweb(path, query):
            page = original_timeweb(path, query)
            if query.get("cursor"):
                page["meta"]["next_cursor"] = query["cursor"]
            return page
        client.timeweb = timeweb
        state = {}
        with self.assertRaises(collector.CollectionFailure) as failure:
            collector.collect_node(client, NODE, state, NOW)
        self.assertEqual(failure.exception.fields["failure_reason"], "non_advancing_cursor")
        self.assertEqual(state, client.saved[-1])
        self.assertEqual(len(client.exported), 1)

    def test_malformed_response_does_not_complete_a_pending_scan(self):
        client = FakeClient([[line()], [line(-1)]])
        state = {}
        collector.collect_node(client, NODE, state, NOW, page_limit=1)
        confirmed = copy.deepcopy(state)
        client.timeweb = Mock(return_value={})
        with self.assertRaises(collector.CollectionFailure) as failure:
            collector.collect_node(client, NODE, state, NOW + 60)
        self.assertEqual(failure.exception.fields["failure_reason"], "invalid_log_response")
        self.assertEqual(state, confirmed)

    def test_rejected_cursor_restarts_from_original_cutoff_without_skipping_backlog(self):
        client = FakeClient([[line(0, "head")], [line(-1, "tail")]])
        state = {}
        collector.collect_node(client, NODE, state, NOW, page_limit=1)
        client.timeweb = Mock(side_effect=collector.RequestFailure(
            urllib.error.HTTPError("private-url", 400, "private-response", {}, None),
            "timeweb/logs", 1, 0.1, "k0scontroller.service"))
        with self.assertRaises(collector.CollectionFailure) as failure:
            collector.collect_node(client, NODE, state, NOW + 86400)
        self.assertEqual(failure.exception.fields["failure_reason"], "cursor_rejected_scan_restarted")
        self.assertEqual(state["1:k0scontroller.service"]["watermark"], NOW - 180)
        retry = FakeClient(client.pages)
        self.assertEqual(collector.collect_node(retry, NODE, state, NOW + 86400), 2)
        self.assertNotIn("cursor", retry.queries[0])
        self.assertEqual({r[2]["body"]["stringValue"] for r in retry.exported}, {"head", "tail"})


class CycleTests(unittest.TestCase):
    def test_failed_scope_does_not_block_other_scopes_or_report_success(self):
        client = Mock()
        client.load_state.return_value = {}
        client.timeweb.return_value = {"nodes": [NODE | {"type": "master"}]}
        metrics = collector.Metrics()
        with patch.object(collector, "collect_node", side_effect=[
                None, collector.CollectionFailure("timestamp_missing"), None, None]) as collect:
            with patch("sys.stdout", new_callable=io.StringIO) as logs:
                collector.collect_cycle(client, metrics)
        self.assertEqual([call.args[4] for call in collect.call_args_list], list(collector.SCOPES))
        self.assertEqual((metrics.errors, metrics.last_success), (1, 0))
        error = json.loads(logs.getvalue())
        self.assertEqual(error["timeweb_log_scope"], collector.SCOPES[1])
        self.assertEqual(error["failure_reason"], "timestamp_missing")

    def test_pending_backfill_is_progress_not_an_error_or_completed_cycle(self):
        client = Mock()
        client.load_state.return_value = {"1:twcp.service": {"scan": {"cursor": "next"}}}
        client.timeweb.return_value = {"nodes": [NODE | {"type": "master"}]}
        metrics = collector.Metrics()
        with patch.object(collector, "collect_node"):
            collector.collect_cycle(client, metrics)
        self.assertEqual((metrics.pending_scans, metrics.errors, metrics.last_success), (1, 0, 0))

    def test_complete_cycle_updates_success_and_clears_backlog(self):
        client = Mock()
        client.load_state.return_value = {}
        client.timeweb.return_value = {"nodes": [NODE | {"type": "master"}]}
        metrics = collector.Metrics()
        metrics.pending_scans = 1
        with patch.object(collector, "collect_node"), patch.object(collector.time, "time", return_value=NOW):
            collector.collect_cycle(client, metrics)
        self.assertEqual((metrics.pending_scans, metrics.errors, metrics.last_success), (0, 0, NOW))

    def test_failure_logs_never_include_exception_text(self):
        with patch("sys.stdout", new_callable=io.StringIO) as logs:
            collector.report_failure(RuntimeError("private-token-response"), collector.Metrics())
        self.assertNotIn("private", logs.getvalue())


class RequestTests(unittest.TestCase):
    def test_get_recovers_from_open_and_response_read_timeouts(self):
        opener = Mock()
        timed_out = io.BytesIO()
        timed_out.read = Mock(side_effect=TimeoutError("response stalled"))
        opener.open.side_effect = [urllib.error.URLError(TimeoutError("connect stalled")),
                                   timed_out, io.BytesIO(b'{"k8s_logs": []}')]
        with patch.object(collector.time, "sleep") as sleep, patch("sys.stdout", new_callable=io.StringIO) as logs:
            result = collector.Client.request(opener, "https://example.com/logs", operation="timeweb/logs",
                                              scope="twcp.service")
        self.assertEqual(result, {"k8s_logs": []})
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertTrue(timed_out.closed)
        retries = [json.loads(line) for line in logs.getvalue().splitlines()]
        self.assertEqual([entry["level"] for entry in retries], ["warning", "warning"])
        self.assertEqual(retries[0]["reason_type"], "TimeoutError")

    def test_timeout_exhaustion_reports_safe_context(self):
        opener = Mock()
        opener.open.side_effect = TimeoutError("private-token-and-response")
        with patch.object(collector.time, "sleep"), patch("sys.stdout", new_callable=io.StringIO) as logs:
            with self.assertRaises(collector.RequestFailure) as raised:
                collector.Client.request(opener, "https://example.com/private-url",
                                         {"Authorization": "Bearer private-token"},
                                         operation="timeweb/logs", scope="twcp.service")
        fields = raised.exception.fields
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(fields["error_type"], "TimeoutError")
        self.assertEqual(fields["attempt"], 3)
        self.assertEqual(fields["operation"], "timeweb/logs")
        self.assertEqual(fields["timeweb_log_scope"], "twcp.service")
        self.assertEqual(fields["request_timeout_seconds"], 20)
        self.assertNotIn("private", logs.getvalue() + json.dumps(fields) + str(raised.exception))

    def test_writes_are_not_retried_after_ambiguous_timeout(self):
        for method in (None, "POST", "PATCH"):
            with self.subTest(method=method):
                opener = Mock()
                opener.open.side_effect = TimeoutError("response stalled")
                with patch.object(collector.time, "sleep") as sleep:
                    with self.assertRaises(collector.RequestFailure):
                        collector.Client.request(opener, "https://example.com", body={"records": []},
                                                 method=method, operation="write")
                self.assertEqual(opener.open.call_count, 1)
                sleep.assert_not_called()

    def test_other_errors_are_not_retried(self):
        for error in (urllib.error.HTTPError("https://example.com", 403, "forbidden", {}, None),
                      urllib.error.URLError("certificate verify failed"), RuntimeError("redirect")):
            with self.subTest(error=type(error).__name__):
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(collector.time, "sleep") as sleep:
                    with self.assertRaises(collector.RequestFailure) as raised:
                        collector.Client.request(opener, "https://example.com", operation="read")
                self.assertEqual(opener.open.call_count, 1)
                self.assertEqual(raised.exception.fields["http_status"], getattr(error, "code", None))
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
