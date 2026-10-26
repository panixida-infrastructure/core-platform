"""Verify frontend identity normalization with the deployed Collector image."""

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
    for signal in ("logs", "metrics", "traces"):
        assert re.search(rf"{signal}/frontend:\n\s+receivers: \[otlp/frontend\]", config)
    assert "kind: HTTPRoute" not in document
    image = re.search(r"image: \"(otel/opentelemetry-collector-contrib:[^\"]+)\"", rendered)[1]
    with tempfile.TemporaryDirectory(prefix="frontend-telemetry-") as directory:
        root = Path(directory)
        root.chmod(0o777)
        test_config = "receivers:\n" + receiver + "processors:\n" + processor
        test_config += "exporters:\n  file:\n    path: /output/received.json\n    flush_interval: 100ms\nservice:\n  pipelines:\n"
        for signal in ("logs", "metrics", "traces"):
            test_config += f"    {signal}:\n      receivers: [otlp/frontend]\n      processors: [resource/frontend]\n      exporters: [file]\n"
        (root / "config.yaml").write_text(test_config, encoding="utf-8")
        name = "frontend-telemetry-" + uuid.uuid4().hex[:10]
        subprocess.run(["docker", "run", "--rm", "-d", "--name", name,
                        "-p", "127.0.0.1::4319", "-v", f"{root}:/output", image,
                        "--config=/output/config.yaml"], check=True, capture_output=True)
        try:
            port = subprocess.check_output(["docker", "port", name, "4319"], text=True).strip().rsplit(":", 1)[1]
            attributes = [{"key": "service.name", "value": {"stringValue": "forged-api"}},
                          {"key": "deployment.environment", "value": {"stringValue": "forged-production"}}]
            now = str(time.time_ns())
            values = {
                "logs": ("resourceLogs", "scopeLogs", "logRecords", {"timeUnixNano": now, "body": {"stringValue": "browser probe"}}),
                "metrics": ("resourceMetrics", "scopeMetrics", "metrics", {"name": "browser_probe", "gauge": {"dataPoints": [{"timeUnixNano": now, "asInt": "1"}]}}),
                "traces": ("resourceSpans", "scopeSpans", "spans", {"traceId": "1" * 32, "spanId": "2" * 16, "name": "browser-probe", "startTimeUnixNano": now, "endTimeUnixNano": now})
            }
            for signal, (resource, scope, records, record) in values.items():
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
            # A payload larger than the previous 64 KiB limit must be accepted.
            large = {"resourceLogs": [{"scopeLogs": [{"logRecords": [
                {"body": {"stringValue": "x" * (body_limit // 2)}}]}]}]}
            request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/logs",
                data=json.dumps(large).encode(), headers={"Content-Type": "application/json",
                    "X-Frontend-Service": "browser-limit-test", "X-Frontend-Environment": "test"})
            with opener.open(request, timeout=5) as response:
                assert response.status == 200
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
                if all(key in content for key in ("resourceLogs", "resourceMetrics", "resourceSpans")):
                    break
                time.sleep(0.1)
            assert all(key in content for key in ("resourceLogs", "resourceMetrics", "resourceSpans")), content
            assert "forged-api" not in content and "forged-production" not in content, content
            assert "tactical-heroes-admin-client-development" in content and "development" in content
            print(f"Frontend logs, metrics and traces: identity normalized; body limit {body_limit} bytes verified; OK")
        except Exception:
            subprocess.run(["docker", "logs", name], check=False)
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
