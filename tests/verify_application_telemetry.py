"""Exercise Blazor dashboards and telemetry alerts with the deployed vmalert version."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap


def verify(rendered):
    names = ("TelemetryCollectorDown", "TelemetryReceiverRefusals", "TelemetryExporterFailures",
             "TelemetryExporterQueueHigh", "BrowserComponentErrors", "ApplicationSlowHttpRequests",
             "ApplicationHigh5xxErrorRate")
    rules = []
    for name in names:
        match = re.search(r'- alert: ' + name + r'\n\s+expr: ([^\n]+)\n\s+for: ([^\n]+)', rendered)
        assert match is not None, name
        rules.append({"alert": name, "expr": match[1], "for": match[2]})
    dashboards = {}
    for match in re.finditer(r"^  ([\w-]+)\.json: \|\n((?:    .*\n|\n)+)", rendered, re.M):
        data = json.loads(textwrap.dedent(match[2]))
        if "panels" in data:
            dashboards[data["uid"]] = data
    browser = dashboards["browser-telemetry"]
    app = dashboards["application-telemetry"]
    pipeline = dashboards["observability-pipeline"]
    for dashboard in (browser, app, pipeline):
        ids = [panel["id"] for panel in dashboard["panels"]]
        assert len(ids) == len(set(ids)), dashboard["uid"]
    assert [v["name"] for v in browser["templating"]["list"]] == ["service", "environment", "instance", "page"]
    assert "$" + "{environment:regex}" not in browser["templating"]["list"][0]["query"]
    assert {v["name"] for v in pipeline["templating"]["list"]} == {"collector", "receiver", "exporter"}
    assert 'service_namespace!="browser"' in app["templating"]["list"][0]["query"]
    assert "http_client_request_duration_seconds_count" not in app["templating"]["list"][0]["query"]
    for panel in app["panels"]:
        for target in panel.get("targets", []):
            if "service_name=~" in target.get("expr", ""):
                assert 'service_namespace!="browser"' in target["expr"], panel["title"]
    service = {"service_name": "test-admin-client-development", "deployment_environment_name": "development"}
    runtime = service | {"service_namespace": "browser", "service_instance_id": "a"}
    central = {"app_kubernetes_io_name": "otel-collector"}

    def series(metric, values, labels=None):
        tags = ",".join(f"{key}={json.dumps(value)}" for key, value in (labels or {}).items())
        return {"series": f"{metric}{{{tags}}}", "values": values}

    healthy = series("up", "1+0x40", central)

    def case(inputs, expected=None, times=("3m", "10m", "25m"), collector=True, selected=None):
        checks = [{"eval_time": when, "alertname": rule["alert"],
                   "exp_alerts": [{"exp_labels": labels, "exp_annotations": {}}
                                  for labels in (expected or {}).get((rule["alert"], when), [])]}
                  for when in times for rule in (rules if selected is None else selected)]
        return {"interval": "1m", "input_series": ([healthy] if collector else []) + inputs,
                "alert_rule_test": checks}

    def fires(name, labels, times=("10m", "25m")):
        return {(name, when): [labels] for when in times}

    tests = [case([]), case([], fires("TelemetryCollectorDown", central, ("3m", "10m", "25m")), collector=False),
             case([series("up", "0+0x40", central)], fires("TelemetryCollectorDown", {} , ("3m", "10m", "25m")), collector=False)]
    for metric, name, dimensions in (
        ("otelcol_receiver_refused_spans_total", "TelemetryReceiverRefusals", {"receiver": "otlp/frontend"}),
        ("otelcol_exporter_send_failed_spans_total", "TelemetryExporterFailures", {"exporter": "otlphttp/victoriatraces"}),
    ):
        labels = central | dimensions
        tests += [case([series(metric, "0+1x40", labels)], fires(name, dimensions)),
                  case([series(metric, "0 1+0x39", labels)]),
                  case([series(metric, "100+0x3 0+0x36", labels)]),
                  case([series(metric, "0+1x40", labels | {"app_kubernetes_io_name": "otel-logs"})])]
    exporter = {"exporter": "otlphttp/victoriametrics"}
    for size, capacity, expected in ((90, 100, True), (80, 100, False), (0, 0, False)):
        tests.append(case([series("otelcol_exporter_queue_size", f"{size}+0x40", central | exporter),
                           series("otelcol_exporter_queue_capacity", f"{capacity}+0x40", central | exporter)],
                          fires("TelemetryExporterQueueHigh", exporter) if expected else {}))
    event = "aspnetcore_components_handle_event_duration_seconds_count"
    navigation = "aspnetcore_components_navigate_total"
    errors = runtime | {"error_type": "System.InvalidOperationException"}
    bad = series(event, "0+1x40", errors)
    good = series(event, "0+9x40", runtime)
    browser_alert = fires("BrowserComponentErrors", service)
    tests += [case([good]), case([bad, good], browser_alert), case([series(navigation, "0+1x40", errors), good], browser_alert),
              case([series(event, "0 1+0x39", errors), good]),
              case([series(event, "0 3+0x39", errors)]),
              case([series(event, "0+1x40", errors | {"service_namespace": "server"}), good]),
              case([bad, good, series(event, "0+100x40", runtime | {"deployment_environment_name": "production"})], browser_alert),
              case([bad, good, series(event, "0+1x40", errors | {"service_instance_id": "b"}),
                    series(event, "0+9x40", runtime | {"service_instance_id": "b"})], browser_alert),
              case([series(event, "100+0x3 0+0x36", errors), good], times=("25m",))]
    http = {"service_name": "test-api-development", "http_route": "/api/factions"}
    count = "http_server_request_duration_seconds_count"
    tests += [case([series(count, "0 1+0x39", http | {"http_response_status_code": "500"})]),
              case([series(count, "0+1x40", http | {"http_response_status_code": "500"}),
                    series(count, "0+9x40", http | {"http_response_status_code": "200"})],
                   fires("ApplicationHigh5xxErrorRate", http, ("3m", "10m", "25m")))]
    for values, expected in (("0 1+0x39", {}), ("0+10x40", fires("ApplicationSlowHttpRequests", http))):
        inputs = [series(count, values, http)]
        for le, buckets in (("1", "0+0x40"), ("10", values), ("+Inf", values)):
            inputs.append(series("http_server_request_duration_seconds_bucket", buckets, http | {"le": le}))
        tests.append(case(inputs, expected))

    def expand(expr, overrides=None):
        variables = {name: ".*" for name in ("service", "environment", "instance", "page", "collector", "receiver", "exporter")}
        variables.update(overrides or {})
        return re.sub(r"\$\{(\w+):regex\}", lambda m: variables[m[1]], expr)

    syntax_rules = []
    for dashboard in (browser, app, pipeline):
        for panel in dashboard["panels"]:
            if dashboard is app and panel["id"] < 120 and panel["id"] not in (5, 7):
                continue
            if panel["type"] in ("logs", "text", "row"):
                continue
            if panel.get("datasource", {}).get("type") == "jaeger":
                continue
            for target in panel.get("targets", []):
                if panel["type"] == "timeseries" and (dashboard is browser or dashboard is app):
                    assert target.get("range") and not target.get("instant"), panel["title"]
                expr = expand(target["expr"])
                if dashboard is browser:
                    assert 'service_namespace="browser"' in expr
                name = f"Panel{dashboard['uid'].replace('-', '')}{panel['id']}{target['refId']}"
                syntax_rules.append({"alert": name, "expr": f"count({expr}) < 0"})
    rules += syntax_rules
    tests.append(case([bad, good], selected=syntax_rules))

    def query_check(name, expr, inputs, expected_value=None):
        expression = (f"absent({expr}) or (sum({expr}) != {expected_value})"
                      if expected_value is not None else f"count({expr}) > 0")
        rule = {"alert": name, "expr": expression}
        rules.append(rule)
        tests.append(case(inputs, selected=[rule], times=("10m",)))

    panels = {p["id"]: p for p in app["panels"]}
    server = {"service_name": "test-admin-development", "deployment_environment_name": "development", "service_instance_id": "a"}
    selected = {"service": "test-admin-development", "environment": "development", "instance": "a"}
    disconnected = expand(panels[122]["targets"][0]["expr"], selected)
    query_check("DisconnectedCircuits", disconnected, [series("aspnetcore_components_circuit_active", "3+0x40", server),
                series("aspnetcore_components_circuit_connected", "1+0x40", server),
                series("aspnetcore_components_circuit_active", "50+0x40", server | {"service_instance_id": "b"})], 2)
    lifetime = {t["refId"]: expand(t["expr"], selected) for t in panels[123]["targets"]}
    assert "B" in lifetime, "Idle circuit panel needs an observed completion count"
    idle = [series("aspnetcore_components_circuit_active", "0+0x40", server)]
    query_check("IdleCircuitsShowZeroCompletions", lifetime["B"], idle, 0)
    query_check("UnknownCircuitsStayEmpty", lifetime["B"], [])
    query_check("CircuitCompletionCount", lifetime["B"], idle + [
                series("aspnetcore_components_circuit_duration_seconds_count", "0+1x40", server)], 10)
    query_check("BrowserCannotPopulateServerCircuits", expand(panels[121]["targets"][0]["expr"]), [
                series("aspnetcore_components_circuit_active", "2+0x40", runtime)])
    for target in panels[124]["targets"]:
        if target["refId"] in ("C", "D"):
            query_check("IdleServerOperations" + target["refId"], expand(target["expr"], selected), idle, 0)
    for pid in (5, 7):
        query_check(f"BrowserDoesNotFakeServerZero{pid}", expand(panels[pid]["targets"][0]["expr"]), [good])
    panels = {p["id"]: p for p in browser["panels"]}
    runtimes = expand(panels[1]["targets"][0]["expr"])
    query_check("ReportingRuntimes", runtimes, [series(navigation, "0+1x40", runtime),
                series(navigation, "0+1x40", runtime | {"service_instance_id": "b"})], 2)
    query_check("ClosedBrowserDoesNotReport", runtimes, [])
    query_check("HealthyHttpRatio", expand(panels[8]["targets"][0]["expr"]),
                [series("http_client_request_duration_seconds_count", "0+10x40", runtime | {"http_response_status_code": "200"})], 0)
    query_check("MissingHttpStaysEmpty", expand(panels[8]["targets"][0]["expr"]), [])

    page = runtime | {"page_route": "/computers/{Id:int}"}
    page_selection = {"service": service["service_name"], "environment": "development", "instance": "a",
                      "page": r"\/computers\/\{Id:int\}"}
    navigation_metric = "browser_page_navigation_duration_seconds"
    page_expression = expand(panels[21]["targets"][0]["expr"], page_selection)
    navigation_labels = page | {"browser_navigation_type": "navigation", "browser_navigation_phase": "first_frame"}
    navigation_input = [series(navigation_metric + "_count", "0+100x40", navigation_labels)]
    for boundary, values in (("0.1", "0+0x40"), ("1", "0+95x40"), ("+Inf", "0+100x40")):
        navigation_input.append(series(navigation_metric + "_bucket", values, navigation_labels | {"le": boundary}))
    unrelated = []
    for labels in (navigation_labels | {"page_route": "/units"},
                   navigation_labels | {"service_instance_id": "b"},
                   navigation_labels | {"deployment_environment_name": "production"},
                   navigation_labels | {"browser_navigation_type": "initial"}):
        for boundary in ("10", "+Inf"):
            unrelated.append(series(navigation_metric + "_bucket", "0+100x40", labels | {"le": boundary}))
    query_check("PageP95UsesRouteRuntimeEnvironmentAndTransitionType", page_expression, navigation_input + unrelated, 1)
    query_check("PageP95MissingMeasurementsStayEmpty", page_expression, unrelated)
    query_check("PageTransitionCount", expand(panels[24]["targets"][0]["expr"], page_selection), navigation_input, 1000)

    vital = "browser_web_vital_lcp_seconds"
    vital_expression = expand(panels[32]["targets"][0]["expr"])
    vital_input = [series(vital, "1+0x40", page), series(vital, "5+0x40", page | {"service_instance_id": "b"})]
    query_check("LatestVitalsUseRuntimeQuantile", vital_expression, vital_input, 4)
    stale_values = " ".join(["1"] * 7 + ["_"] * 34)
    query_check("StaleVitalsAreNotReportingRuntimes", vital_expression, [series(vital, stale_values, page)])
    startup_expression = expand(panels[31]["targets"][0]["expr"], page_selection)
    startup_metric = "browser_wasm_startup_duration_seconds_bucket"
    startup_labels = page | {"browser_wasm_phase": "runtime"}
    startup_input = [series(startup_metric, values, startup_labels | {"le": boundary})
                     for boundary, values in (("0.1", "0+0x40"), ("1", "95+0x40"), ("+Inf", "100+0x40"))]
    unrelated_startups = [series(startup_metric, "100+0x40", labels | {"le": boundary})
                          for labels in (startup_labels | {"page_route": "/units"},
                                         startup_labels | {"service_instance_id": "b"},
                                         startup_labels | {"deployment_environment_name": "production"})
                          for boundary in ("10", "+Inf")]
    query_check("StartupP95UsesOneShotRuntimeMeasurements", startup_expression, startup_input + unrelated_startups, 1)
    query_check("UnrelatedStartupsStayEmpty", startup_expression, unrelated_startups)
    stale_startup = [series(startup_metric, " ".join(["100"] * 7 + ["_"] * 34), startup_labels | {"le": boundary})
                     for boundary in ("1", "+Inf")]
    query_check("StaleStartupsAreNotReportingRuntimes", startup_expression, stale_startup)
    byte_metric = "browser_wasm_resource_decoded_size_bytes_total"
    byte_input = [series(byte_metric, f"{value}+0x40", page | {"service_instance_id": instance,
                  "browser_resource_type": resource}) for instance, resource, value in
                  (("a", "wasm", 100), ("a", "javascript", 50), ("b", "wasm", 200), ("b", "javascript", 150))]
    query_check("FrameworkBytesSumTypesBeforeAveragingRuntimes", expand(panels[34]["targets"][0]["expr"]), byte_input, 250)

    app_panels = {p["id"]: p for p in app["panels"]}
    active = "yarp_proxy_active_requests"
    query_check("YarpActiveRequestsRespectRuntimeAndExcludeBrowsers", expand(app_panels[131]["targets"][0]["expr"], selected),
                [series(active, "2+0x40", server), series(active, "50+0x40", server | {"service_instance_id": "b"}),
                 series(active, "100+0x40", runtime)], 2)
    query_check("AbsentYarpMetricsStayEmpty", expand(app_panels[131]["targets"][0]["expr"]), [])
    yarp_errors = expand(app_panels[132]["targets"][0]["expr"], selected)
    query_check("ReportingYarpWithoutErrorsShowsZero", yarp_errors, [series(active, "0+0x40", server)], 0)
    query_check("UnknownYarpErrorsStayEmpty", yarp_errors, [])
    query_check("StaleYarpCannotFakeZeroErrors", yarp_errors,
                [series(active, " ".join(["0"] * 7 + ["_"] * 34), server)])
    query_check("OtherRuntimeCannotFakeZeroErrors", yarp_errors,
                [series(active, "0+0x40", server | {"service_instance_id": "b"})])
    query_check("BrowserCannotFakeZeroYarpErrors", expand(app_panels[132]["targets"][0]["expr"]),
                [series(active, "0+0x40", runtime)])
    query_check("KnownYarpErrorsDoNotAddFallback", "count(" + yarp_errors + ")",
                [series(active, "0+0x40", server),
                 series("yarp_proxy_errors_total", "0+1x40", server | {"error_type": "Request"})], 1)
    query_check("FirstYarpErrorIsNotReplacedByZero", yarp_errors,
                [series(active, "0+0x40", server),
                 series("yarp_proxy_errors_total", " ".join(["_"] * 10 + ["1"]),
                        server | {"error_type": "Request"})])
    query_check("YarpErrorsKeepReasons", "count(" + expand(app_panels[132]["targets"][0]["expr"]) + ")",
                [series("yarp_proxy_errors_total", "0+1x40", server | {"error_type": "Request"}),
                 series("yarp_proxy_errors_total", "0+1x40", server | {"error_type": "RequestCanceled"}),
                 series("yarp_proxy_errors_total", "0+1x40", runtime | {"error_type": "BrowserError"})], 2)
    image = re.search(r'image: "(victoriametrics/vmalert:[^"]+)"', rendered)[1]
    with tempfile.TemporaryDirectory(prefix="application-telemetry-") as directory:
        work = Path(directory)
        (work / "rules.json").write_text(json.dumps({"groups": [{"name": "application", "rules": rules}]}))
        (work / "tests.json").write_text(json.dumps({"rule_files": ["/work/rules.json"],
            "evaluation_interval": "1m", "tests": tests}))
        subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/work", image.replace("/vmalert:", "/vmalert-tool:"),
                        "unittest", "--disableAlertgroupLabel", "--files=/work/tests.json"], check=True)
    print(f"Application telemetry: {len(tests)} scenarios and {len(syntax_rules)} panel queries verified.", flush=True)


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
