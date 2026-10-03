"""Validate panel/variable references and execute annotation templates with vmalert-tool.

Usage: python3 -B tests/verify_metric_links.py /tmp/core-platform-workloads.yaml
Requires Docker; uses only Python's standard library.
"""

import json
import html
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlencode, urlsplit


def verify(rendered):
    dashboards = {}
    for match in re.finditer(r"^  ([\w-]+)\.json: \|\n((?:    .*\n|\n)+)", rendered, re.M):
        dashboard = json.loads(textwrap.dedent(match[2]))
        dashboards[dashboard["uid"]] = dashboard

    links = dict(re.findall(
        r"- alert: (\w+)\n(?:(?!- alert:).)*?dashboard_url: (\"[^\n]+\")",
        rendered, re.S))
    assert len(links) == 33, f"Expected 33 metric alert links, got {len(links)}"
    links = {name: json.loads(value) for name, value in links.items()}
    for name, link in links.items():
        parsed = urlsplit(re.sub(r"{{.*?}}", "", link))
        dashboard = dashboards[parsed.path.split("/")[2]]
        params = parse_qs(parsed.query, keep_blank_values=True)
        panel = params.get("viewPanel")
        if panel:
            assert int(panel[0].removeprefix("panel-")) in {
                item["id"] for item in dashboard["panels"]}, name
        variables = {item["name"] for item in dashboard.get("templating", {}).get("list", [])}
        assert {key[4:] for key in params if key.startswith("var-")} <= variables, name

    labels = {"service_name": 'api &/"test', "service_instance_id": "node &/ 1",
              "namespace": "test-ns", "pod": "test-pod", "container": "api"}
    cases = [
        ("ApplicationSlowHttpRequests", "application-telemetry", 15, {"service": labels["service_name"]}),
        ("HostHighCpu", "infrastructure-nodes", 1, {"node": labels["service_instance_id"]}),
        ("ApplicationHighMemoryUsage", "kubernetes-workloads", 2,
         {"container": "api", "namespace": "test-ns", "pod": "test-pod"}),
        ("TelegramAlertGatewayDeliveryFailures", "core-platform-overview", None, {}),
    ]
    rules, checks = [], []
    for name, dashboard, panel, variables in cases:
        rules.append({"alert": name, "expr": "metric_link_fixture", "annotations": {"dashboard_url": links[name]}})
        params = {"orgId": "1", "from": "-3600000", "to": "now", "timezone": "browser", "refresh": "30s"}
        if panel:
            params["viewPanel"] = f"panel-{panel}"
        params.update({f"var-{key}": value for key, value in variables.items()})
        expected = f"https://grafana.panixida.ru/d/{dashboard}/{dashboard}?{urlencode(params)}"
        checks.append({"eval_time": "1m", "alertname": name, "exp_alerts": [
            {"exp_labels": labels, "exp_annotations": {"dashboard_url": expected}}]})

    image = re.search(r'image: "(victoriametrics/vmalert:[^"]+)"', rendered)[1]
    with tempfile.TemporaryDirectory(prefix="metric-links-") as directory:
        work = Path(directory)
        (work / "rules.json").write_text(json.dumps({"groups": [{"name": "links", "rules": rules}]}))
        series = "metric_link_fixture{" + ",".join(f"{key}={json.dumps(value)}" for key, value in labels.items()) + "}"
        (work / "tests.json").write_text(json.dumps({
            "rule_files": ["/work/rules.json"], "evaluation_interval": "1m",
            "tests": [{"interval": "1m", "input_series": [{"series": series, "values": "1 1"}],
                       "alert_rule_test": checks}]}))
        subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/work", "-w", "/work",
                        image.replace("/vmalert:", "/vmalert-tool:"), "unittest",
                        "--disableAlertgroupLabel", "--files=/work/tests.json"], check=True)
    print("33 dashboard links: panels and variables exist; contextual annotations render correctly.", flush=True)

    # Mixed emergency groups must retain each alert's link even without CommonAnnotations.
    document = next(doc for doc in rendered.split("\n---") if "  name: alertmanager\n" in doc)
    template = textwrap.dedent(document.split("  telegram.tmpl: |\n", 1)[1])
    image = re.search(r'image: "(prom/alertmanager:[^"]+)"', rendered)[1]
    with tempfile.TemporaryDirectory(prefix="metric-emergency-") as directory:
        work = Path(directory)
        # amtool runs as nobody; TemporaryDirectory is owner-only on Linux.
        work.chmod(0o755)
        (work / "telegram.tmpl").write_text(template, encoding="utf-8")
        urls = [f"https://grafana.panixida.ru/d/demo/demo?var-service=api-{i}&viewPanel=panel-{i}" for i in range(1, 7)]
        payload = {"Status": "firing", "CommonAnnotations": {}, "Alerts": [
            {"Status": "firing", "Labels": {"alertname": f"Test-{i}"},
             "Annotations": {"dashboard_url": url}} for i, url in enumerate(urls)]}
        def render_payload(data):
            (work / "data.json").write_text(json.dumps(data))
            result = subprocess.run([
                "docker", "run", "--rm", "-v", f"{work}:/work", "--entrypoint=/bin/amtool", image,
                "template", "render", "--template.glob=/work/telegram.tmpl", "--template.type=html",
                '--template.text={{ template "telegram.panixida.message" . }}',
                "--template.data=/work/data.json"], check=False, capture_output=True, text=True, encoding="utf-8")
            assert result.returncode == 0, result.stderr + result.stdout
            assert len(result.stdout.encode("utf-16-le")) // 2 < 4096, "Emergency message exceeds limit"
            ET.fromstring("<message>" + result.stdout + "</message>")
            return result.stdout

        message = html.unescape(render_payload(payload))
        assert message.count(">Grafana</a>") == 5, message
        assert message.count("\n\n• • •\n\n") == 4, message
        assert message.count('\n\n🔗 <a href=') == 5, message
        assert all(url in message for url in urls[:5]), message
        assert urls[5] not in message, message
        assert len(message) < 4096, "Emergency group exceeds Telegram limit"
        assert "1 alerts omitted" in message, message

        mixed = {"Alerts": [
            {"Status": status,
             "Labels": {"alertname": f"Test-{port}", "severity": severity, "service_name": "demo-api",
                        "alert_owner": "tests", "environment": "demo",
                        "http_url": "https://user:secret@api.example/health?token=secret",
                        "instance": f"worker:{port}"},
             "Annotations": {"summary": "Synthetic preview", "description": "No real outage."}}
            for status, severity, port in [("firing", "critical", 8080), ("resolved", "warning", 8081)]]}
        message = render_payload(mixed)
        assert "🔥 <b>Alerts firing</b>" in message, message
        assert "Firing: <b>1</b> | Resolved: <b>1</b>" in message, message
        assert "🔥 <b>Test-8080</b> · CRITICAL" in message, message
        assert "✅ <b>Test-8081</b> · WARNING" in message, message
        assert message.count("📦 demo-api") == 2 and message.count("📖 No real outage.") == 2, message
        assert message.count('<a href="https://api.example/">api.example:443</a>') == 2, message
        for port in (8080, 8081):
            assert f'🖥 Instance: <a href="http://worker:{port}/">worker:{port}</a>' in message, message
        assert "🏷" not in message and "⚙️" not in message and "<code>" not in message, message
        assert "secret" not in message and "/health" not in message, message
        assert ">Grafana</a>\n🔗 <a href=" in message, message

        hostile = '<&"🙂>' * 500
        for alert in mixed["Alerts"]:
            alert["Labels"].update(alertname=hostile, service_name=hostile, instance=hostile)
            alert["Annotations"].update(summary=hostile, description=hostile,
                                        dashboard_url="https://grafana.panixida.ru/d/demo/demo?filter=" + "x" * 800)
        message = render_payload({"Alerts": mixed["Alerts"] * 3})
        assert "alerts omitted" in message, message
        assert "Description omitted" in message, message
        assert "&lt;" in message, message
        assert ">Alertmanager</a>" in message, message
    print("Emergency alerts: unified style, distinct linked instances, valid bounded HTML and group cap.", flush=True)


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
