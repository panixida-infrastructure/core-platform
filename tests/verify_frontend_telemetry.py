"""Verify telemetry resource normalization with the deployed Collector image."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
import uuid


def verify(rendered):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    document = next(doc for doc in rendered.split("\n---")
                    if "kind: ConfigMap\n" in doc and "  name: otel-collector\n" in doc)
    config = textwrap.dedent(document.split("  config.yml: |\n", 1)[1])
    receiver = re.search(r"^  otlp/frontend:\n(?:    .*\n|\n)+", config, re.M)[0]
    body_limit = int(re.search(r"max_request_body_size: (\d+)\s*$", receiver, re.M)[1])
    assert body_limit > 0
    processor = re.search(r"^  resource/frontend:\n(?:    .*\n|\n)+", config, re.M)[0]
    server_receiver = re.search(r"^  otlp:\n(?:    .*\n|\n)+", config, re.M)[0]
    common_processor = re.search(r"^  resource/core-platform:\n(?:    .*\n|\n)+", config, re.M)[0]
    for signal in ("logs", "metrics", "traces"):
        assert re.search(rf"{signal}/frontend:\n\s+receivers: \[otlp/frontend\]", config)
    assert "kind: HTTPRoute" not in document
    image = re.search(r"image: \"(otel/opentelemetry-collector-contrib:[^\"]+)\"", rendered)[1]
    with tempfile.TemporaryDirectory(prefix="frontend-telemetry-") as directory:
        root = Path(directory)
        root.chmod(0o777)
        test_config = "receivers:\n" + receiver + server_receiver + "processors:\n" + processor + common_processor
        test_config += "exporters:\n  file:\n    path: /output/received.json\n    flush_interval: 100ms\nservice:\n  pipelines:\n"
        for signal in ("logs", "metrics", "traces"):
            test_config += f"    {signal}/frontend:\n      receivers: [otlp/frontend]\n      processors: [resource/frontend, resource/core-platform]\n      exporters: [file]\n"
            test_config += f"    {signal}/server:\n      receivers: [otlp]\n      processors: [resource/core-platform]\n      exporters: [file]\n"
        (root / "config.yaml").write_text(test_config, encoding="utf-8")
        name = "frontend-telemetry-" + uuid.uuid4().hex[:10]
        subprocess.run(["docker", "run", "--rm", "-d", "--name", name,
                        "-p", "127.0.0.1::4319", "-p", "127.0.0.1::4318", "-v", f"{root}:/output", image,
                        "--config=/output/config.yaml"], check=True, capture_output=True)
        try:
            ports = {}
            for _ in range(50):
                for target in ("4318", "4319"):
                    binding = subprocess.check_output(["docker", "port", name, target], text=True).strip()
                    if binding:
                        ports[target] = binding.rsplit(":", 1)[1]
                if len(ports) == 2:
                    break
                time.sleep(0.1)
            assert len(ports) == 2, ports
            port, server_port = ports["4319"], ports["4318"]
            attributes = [{"key": "service.name", "value": {"stringValue": "forged-api"}},
                          {"key": "deployment.environment", "value": {"stringValue": "forged-production"}},
                          {"key": "deployment.environment.name", "value": {"stringValue": "forged-staging"}}]
            expected = {}
            now = str(time.time_ns())
            values = {
                "logs": ("resourceLogs", "scopeLogs", "logRecords", {"timeUnixNano": now, "body": {"stringValue": "browser probe"}}),
                "metrics": ("resourceMetrics", "scopeMetrics", "metrics", {"name": "browser_probe", "gauge": {"dataPoints": [{"timeUnixNano": now, "asInt": "1"}]}}),
                "traces": ("resourceSpans", "scopeSpans", "spans", {"traceId": "1" * 32, "spanId": "2" * 16, "name": "browser-probe", "startTimeUnixNano": now, "endTimeUnixNano": now})
            }
            for signal, (resource, scope, records, record) in values.items():
                expected[signal, "tactical-heroes-admin-client-development"] = ("development", "browser")
                payload = {resource: [{"resource": {"attributes": attributes}, scope: [{records: [record]}]}]}
                request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/{signal}",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json",
                        "X-Frontend-Service": "tactical-heroes-admin-client-development", "X-Frontend-Environment": "development"})
                for attempt in range(150):
                    try:
                        with opener.open(request, timeout=5) as response:
                            assert response.status == 200
                        break
                    except (urllib.error.URLError, ConnectionResetError):
                        if attempt == 149:
                            raise
                        time.sleep(0.2)
                for case, environment_attributes, environment in (
                    ("legacy", {"deployment.environment": "development"}, "development"),
                    ("canonical", {"deployment.environment.name": "staging"}, "staging"),
                    ("both", {"deployment.environment": "production", "deployment.environment.name": "test"}, "test"),
                    ("missing", {}, None),
                ):
                    service = "server-" + case
                    resource_attributes = {"service.name": service, **environment_attributes}
                    payload = {resource: [{"resource": {"attributes": [
                        {"key": key, "value": {"stringValue": value}} for key, value in resource_attributes.items()
                    ]}, scope: [{records: [record]}]}]}
                    request = urllib.request.Request(f"http://127.0.0.1:{server_port}/v1/{signal}",
                        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                    with opener.open(request, timeout=5) as response:
                        assert response.status == 200
                    expected[signal, service] = (environment, None)
            large = {"resourceLogs": [{"scopeLogs": [{"logRecords": [
                {"body": {"stringValue": "x" * (body_limit // 2)}}]}]}]}
            request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/logs",
                data=json.dumps(large).encode(), headers={"Content-Type": "application/json",
                    "X-Frontend-Service": "browser-limit-test", "X-Frontend-Environment": "test"})
            with opener.open(request, timeout=5) as response:
                assert response.status == 200
            expected["logs", "browser-limit-test"] = ("test", "browser")
            request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/logs",
                data=json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": [
                    {"body": {"stringValue": "x" * body_limit}}]}]}]}).encode(),
                headers={"Content-Type": "application/json"})
            try:
                opener.open(request, timeout=5).close()
                raise AssertionError("Oversized payload was accepted")
            except urllib.error.HTTPError as error:
                message = error.read().decode()
                assert error.code in (400, 413) and "too large" in message.lower(), (error.code, message)
            output = root / "received.json"
            for _ in range(50):
                content = output.read_text(encoding="utf-8") if output.exists() else ""
                received = {}
                for line in content.splitlines():
                    try:
                        batch = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for signal, (resource, _, _, _) in values.items():
                        for item in batch.get(resource, []):
                            attrs = {attribute["key"]: attribute["value"].get("stringValue")
                                     for attribute in item["resource"].get("attributes", [])}
                            received[signal, attrs.get("service.name")] = attrs
                if expected.keys() <= received.keys():
                    break
                time.sleep(0.1)
            assert expected.keys() <= received.keys(), (expected.keys() - received.keys(), content)
            for key, (environment, namespace) in expected.items():
                attrs = received[key]
                assert attrs.get("deployment.environment.name") == environment, (key, attrs)
                assert "deployment.environment" not in attrs, (key, attrs)
                assert attrs.get("service.namespace") == namespace, (key, attrs)
                assert attrs["platform.name"] == "core-platform", (key, attrs)
            assert not any(value in content for value in ("forged-api", "forged-production", "forged-staging")), content
            print(f"Logs, metrics and traces: {len(expected)} normalized resources; body limit {body_limit} bytes verified; OK")
        except Exception:
            subprocess.run(["docker", "logs", name], check=False)
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
