"""Exercise rendered filelog operators with the deployed Collector image.

Usage: python3 -B tests/verify_log_levels.py /tmp/core-platform-workloads.yaml
Requires Docker and Node.js; uses only Python's standard library.
"""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid


def configmap(rendered, name, key):
    document = next(doc for doc in rendered.split("\n---")
                    if "kind: ConfigMap\n" in doc and f"  name: {name}\n" in doc)
    block = document.split(f"  {key}: |\n", 1)[1]
    return textwrap.dedent(block).rstrip() + "\n"


def verify_grafana_rules(rendered):
    datasource = configmap(rendered, "grafana-datasources", "datasources.yml")
    rules = re.findall(r"field: severity_text\s+value: '([^']+)'\s+level: (\w+)", datasource)
    assert len(rules) == 6, "Expected six severity rules"
    aliases = {
        "critical": ["Critical", "CRIT", "FATAL", "Fatal2", "panic", "DPANIC"],
        "error": ["Error", "ERROR", "err", "error4"],
        "warning": ["Warning", "WARN", "warning", "Warn3"],
        "info": ["Information", "INFO", "info", "Info", "iNfO", "Informational", "INFO2"],
        "debug": ["Debug", "DEBUG", "debug3"],
        "trace": ["Trace", "TRACE", "trace4"],
    }
    examples = [(value, [expected]) for expected, values in aliases.items() for value in values]
    for value in ["", "Unspecified", "unknown", "error fetching data", "INFO5"]:
        examples.append((value, []))
    # The plugin calls new RegExp(rule.value), without flags. A Python-only
    # regex test would miss expressions such as (?i) that fail in the browser.
    subprocess.run(["node", "-e", """
const {rules, examples} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
for (const [value, expected] of examples) {
  const actual = rules.filter(([pattern]) => new RegExp(pattern).test(value)).map(([, level]) => level);
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(JSON.stringify({value, actual, expected}));
  }
}
"""], input=json.dumps({"rules": rules, "examples": examples}), text=True, check=True)
    print("Grafana severity aliases and unknown values: OK", flush=True)


def fixtures():
    cases = []

    def add(container, body, severity):
        cases.append((container, body, severity))

    # Preserve existing JSON, logfmt and klog parsing.
    add("app", '{"level":"error","msg":"failed"}', 17)
    add("app", '{"level":"warning","msg":"retry"}', 13)
    add("app", 'time="2026-10-03T08:00:00Z" level=info msg="failed previously"', 9)
    add("app", 'level=critical msg="cannot start"', 21)
    add("konnectivity-agent", "I1003 08:00:00.123456 1 server.go:42] ready", 9)
    add("external-attacher", "E1003 08:00:00.123456 1 server.go:42] failed", 17)

    # Samples of each observed plain-text prefix, including warnings/errors.
    add("gluetun", "2026-10-03T08:00:00Z INFO [http proxy] request completed", 9)
    add("gluetun", "2026-10-03T12:00:00+04:00 ERROR [http proxy] request failed", 17)
    add("otel-collector", "2026-10-03T08:00:00.123Z\twarn\tinternal/transaction.go:152\tFailed to scrape", 13)
    add("sonarqube", "2026.10.03 08:00:00 INFO  ce[][worker] Execute task", 9)
    add("sonarqube", "2026.10.03 08:00:00 ERROR web[][worker] Task failed", 17)
    add("envoy-gateway", "1.7910146344731445e+09\tinfo\txds\topen delta watch", 9)
    add("envoy-gateway", "1791014634.473\tdebug\txds\twatch details", 5)
    add("management-controller", "2026-10-03T08:00:00Z\tINFO\tprojects/handler.go:80\tHealth changed", 9)
    add("openbao", "2026-10-03T08:00:00.123Z [INFO]  expiration: revoked lease", 9)
    add("openbao", "2026-10-03T08:00:00.123Z [WARN]  core: slow request", 13)
    add("openbao", "2026-10-03T08:00:00.123Z [ERROR] core: request failed", 17)
    for prefix, severity in [("trce", 1), ("dbug", 5), ("info", 9),
                             ("warn", 13), ("fail", 17), ("crit", 21), ("Error", 17)]:
        add("migrator", f"{prefix}: Example.Database[1]", severity)

    # Access logs use status, never arbitrary words in the request/message.
    for status, severity in [(101, 9), (200, 9), (307, 9), (404, 13), (503, 17)]:
        add("envoy", json.dumps({"method": "GET", "response_code": status}), severity)
        add("envoy", json.dumps({"response_code": status, "path": "/error"}), severity)
        for container in ["squid", "log-forwarder"]:
            add(container, f"1791010988.675 182 192.0.2.1 TCP_TUNNEL/{status} 6803 CONNECT example.org:443 -", severity)
    add("envoy", '{"level":"error","response_code":200}', 17)

    # Do not manufacture a severity for missing levels/statuses or other sources.
    add("envoy", '{"response_code":0,"response_flags":"DC"}', 0)
    add("envoy", '{"response_code":null}', 0)
    add("app", '{"response_code":503}', 0)
    add("squid", "1791010988.675 182 192.0.2.1 NONE_NONE/000 0 - -", 0)
    add("app", "1791010988.675 182 192.0.2.1 TCP_TUNNEL/503 6803 CONNECT example.org:443 -", 0)
    add("app", "Request had an error but has no severity prefix", 0)
    add("app", "2026-10-03T08:00:00Z request completed with previous error", 0)
    add("app", "2026-10-03T08:00:00Z INFOGRAPHS are ready", 0)
    add("migrator", "      SELECT 1;", 0)
    return cases


def records(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            batch = json.loads(line)
        except json.JSONDecodeError:
            continue  # The exporter may still be writing the last line.
        for resource in batch.get("resourceLogs", []):
            attributes = {a["key"]: a["value"].get("stringValue")
                          for a in resource["resource"].get("attributes", [])}
            for scope in resource.get("scopeLogs", []):
                for log in scope.get("logRecords", []):
                    result.append((attributes.get("k8s.container.name"), log))
    return result


def verify_collector(rendered):
    collector = configmap(rendered, "otel-logs", "config.yml")
    operators = collector.split("    operators:\n", 1)[1].split("\nprocessors:", 1)[0]
    image = re.search(r"image: [\"']?(otel/opentelemetry-collector-contrib:[^\s\"']+)", rendered)[1]
    cases = fixtures()
    name = "log-level-test-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="log-level-test-") as directory:
        root = Path(directory)
        output = root / "output"
        output.mkdir(mode=0o777)
        output.chmod(0o777)
        pod = root / "pods" / "observability_log-test_00000000-0000-4000-8000-000000000000"
        for container, body, _ in cases:
            folder = pod / container
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / "0.log").open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(f"2026-10-03T08:00:00.000000000Z stdout F {body}\n")
        config = ("receivers:\n  filelog/test:\n"
                  "    include: [/var/log/pods/*/*/*.log]\n"
                  "    start_at: beginning\n    include_file_path: true\n"
                  "    poll_interval: 100ms\n    operators:\n" + operators + "\n"
                  "exporters:\n  file:\n    path: /output/logs.json\n    flush_interval: 100ms\n"
                  "service:\n  pipelines:\n    logs:\n"
                  "      receivers: [filelog/test]\n      exporters: [file]\n")
        (root / "config.yml").write_text(config, encoding="utf-8")
        with (root / "collector.log").open("w", encoding="utf-8") as collector_log:
            process = subprocess.Popen([
                "docker", "run", "--rm", "--name", name,
                "-v", f"{root / 'config.yml'}:/etc/otelcol/config.yml:ro",
                "-v", f"{root / 'pods'}:/var/log/pods:ro",
                "-v", f"{output}:/output", image, "--config=/etc/otelcol/config.yml",
            ], stdout=collector_log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 60
                received = []
                while time.monotonic() < deadline and process.poll() is None:
                    received = records(output / "logs.json")
                    if len(received) >= len(cases):
                        break
                    time.sleep(0.2)
                assert len(received) == len(cases), (
                    f"Expected {len(cases)} records, got {len(received)}\n"
                    + (root / "collector.log").read_text(encoding="utf-8"))
                actual = {(container, log["body"]["stringValue"]): log
                          for container, log in received}
                assert len(actual) == len(cases), "Lost or duplicated a fixture"
                for container, body, severity in cases:
                    log = actual[container, body]
                    assert log.get("severityNumber", 0) == severity, (container, body, log)
                    if severity:
                        assert log["severityText"] in ["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"], log
                print(f"{image}: {len(cases)} filelog records, severity and unchanged body: OK", flush=True)
            finally:
                subprocess.run(["docker", "stop", "--time", "2", name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                process.wait(timeout=15)


if __name__ == "__main__":
    manifests = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
    verify_grafana_rules(manifests)
    verify_collector(manifests)
