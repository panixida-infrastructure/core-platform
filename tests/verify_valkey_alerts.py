"""Exercise Valkey firing/recovery/no-data rules and dashboard queries with vmalert-tool."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def verify(rendered):
    rules = [{"alert": match[1], "expr": match[2], "for": match[3]}
             for match in re.finditer(
                 r'- alert: (ManagedValkey\w+)\n\s+expr: ([^\n]+)\n\s+for: ([^\n]+)', rendered)]
    assert len(rules) == 14, "Missing Valkey rules"
    database = 'service_name="timeweb-dbaas-valkey"'
    host = 'service_name="timeweb-dbaas-valkey-node"'
    disk_labels = {"service_name": "timeweb-dbaas-valkey-node", "fstype": "ext4",
                   "device": "/dev/vda1", "mountpoint": "/"}
    disk = ",".join(f"{key}={json.dumps(value)}" for key, value in disk_labels.items())

    def db(metric, labels=""):
        return metric + "{" + database + ("," + labels if labels else "") + "}"

    defaults = {
        db("up"): "1+0x50", db("redis_up"): "1+0x50",
        db("redis_exporter_last_scrape_error"): "0+0x50",
        db("redis_memory_used_bytes"): "100+0x50", db("redis_memory_max_bytes"): "1000+0x50",
        db("redis_mem_not_counted_for_eviction_bytes"): "0+0x50",
        db("redis_errors_total", 'err="OOM"'): "0+0x50",
        db("redis_commands_total", 'cmd="set"'): "0+120x50",
        db("redis_commands_duration_seconds_total", 'cmd="set"'): "0+0.1x50",
        db("redis_commands_failed_calls_total", 'cmd="set"'): "0+0x50",
        db("redis_commands_rejected_calls_total", 'cmd="set"'): "0+0x50",
        db("redis_rejected_connections_total"): "0+0x50",
        db("redis_aof_enabled"): "1+0x50", db("redis_aof_last_write_status"): "1+0x50",
        db("redis_aof_last_bgrewrite_status"): "1+0x50", db("redis_rdb_last_bgsave_status"): "1+0x50",
        db("redis_evicted_keys_total"): "0+0x50",
        "up{" + host + "}": "1+0x50", "node_time_seconds{" + host + "}": "946684800+60x50",
        "node_filesystem_avail_bytes{" + disk + "}": "90+0x50",
        "node_filesystem_size_bytes{" + disk + "}": "100+0x50",
    }

    def case(changes=None, firing=(), persistent=False, missing=False):
        series = {} if missing else defaults | (changes or {})
        checks = []
        for when in ("0m", "1m", "16m", "30m"):
            for rule in rules:
                active = rule["alert"] in firing and (
                    when == "16m" or (persistent and when == "30m"))
                labels = disk_labels if rule["alert"].startswith("ManagedValkeyDisk") else {}
                checks.append({"eval_time": when, "alertname": rule["alert"],
                               "exp_alerts": [{"exp_labels": labels, "exp_annotations": {}}] if active else []})
        return {"interval": "1m", "input_series": [{"series": key, "values": value}
                for key, value in series.items() if value is not None], "alert_rule_test": checks}

    tests = [
        case(),
        case({db("redis_memory_used_bytes"): "850+0x19 100+0x30"}, ["ManagedValkeyMemoryHigh"]),
        case({db("redis_memory_used_bytes"): "950+0x19 100+0x30"},
             ["ManagedValkeyMemoryHigh", "ManagedValkeyMemoryCritical"]),
        # AOF/replication buffers excluded from eviction accounting do not cause false memory alerts.
        case({db("redis_memory_used_bytes"): "950+0x50", db("redis_mem_not_counted_for_eviction_bytes"): "200+0x50"}),
        case({db("redis_up"): "0+0x19 1+0x30", db("redis_exporter_last_scrape_error"): "1+0x19 0+0x30"},
             ["ManagedValkeyDown"]),
        case({db("up"): "0+0x19 1+0x30"}, ["ManagedValkeyMetricsUnavailable"]),
        case({db("redis_exporter_last_scrape_error"): "1+0x19 0+0x30"}, ["ManagedValkeyMetricsUnavailable"]),
        case({db("redis_memory_max_bytes"): None}, ["ManagedValkeyMetricsUnavailable"], persistent=True),
        case(firing=["ManagedValkeyMetricsUnavailable", "ManagedValkeyHostMetricsUnavailable"],
             persistent=True, missing=True),
        # A collector that stops after initial samples must also produce missing-data alerts.
        case({key: "1 1 stale" for key in defaults},
             ["ManagedValkeyMetricsUnavailable", "ManagedValkeyHostMetricsUnavailable"], persistent=True),
        case({db("redis_errors_total", 'err="OOM"'): "0+1x19 20+0x30"}, ["ManagedValkeyOutOfMemory"]),
        case({db("redis_errors_total", 'err="OOM"'): "20+0x19 0+0x30"}),
        case({db("redis_commands_failed_calls_total", 'cmd="set"'): "0+1x19 20+0x30"},
             ["ManagedValkeyWriteFailures"]),
        case({db("redis_commands_rejected_calls_total", 'cmd="set"'): "0+1x19 20+0x30"},
             ["ManagedValkeyWriteFailures"]),
        case({db("redis_rejected_connections_total"): "0+1x19 20+0x30"}, ["ManagedValkeyRejectedConnections"]),
        case({db("redis_commands_duration_seconds_total", 'cmd="set"'): "0+12x19 240+0.1x30"},
             ["ManagedValkeyCommandLatency"]),
        case({db("redis_commands_total", 'cmd="set"'): "0+1x50",
              db("redis_commands_duration_seconds_total", 'cmd="set"'): "0+0.1x50"}),
        case({db("redis_commands_total", 'cmd="set"'): "0+0x50",
              db("redis_commands_duration_seconds_total", 'cmd="set"'): "0+0x50"}),
        case({db("redis_aof_last_write_status"): "0+0x19 1+0x30"}, ["ManagedValkeyAofFailure"]),
        case({db("redis_aof_last_bgrewrite_status"): "0+0x19 1+0x30"}, ["ManagedValkeyAofFailure"]),
        case({db("redis_aof_enabled"): "0+0x50", db("redis_aof_last_write_status"): "0+0x50"}),
        case({db("redis_rdb_last_bgsave_status"): "0+0x19 1+0x30"}, ["ManagedValkeyRdbFailure"]),
        case({"node_filesystem_avail_bytes{" + disk + "}": "15+0x19 90+0x30"}, ["ManagedValkeyDiskLow"]),
        case({"node_filesystem_avail_bytes{" + disk + "}": "5+0x19 90+0x30"},
             ["ManagedValkeyDiskLow", "ManagedValkeyDiskCritical"]),
        case({db("redis_evicted_keys_total"): "0+1x19 20+0x30"}, ["ManagedValkeyUnexpectedEvictions"]),
    ]
    dashboard = json.loads(re.search(r"  valkey-managed\.json: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1])
    dashboard_checks = []
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            name = f"ValkeyDashboard{panel['id']}{target['refId']}"
            # Execute every expression even when its source series is absent from this fixture.
            rules.append({"alert": name, "expr": f"count({target['expr']}) >= 0 or vector(1)"})
            dashboard_checks.append({"eval_time": "16m", "alertname": name,
                                     "exp_alerts": [{"exp_labels": {}, "exp_annotations": {}}]})
    healthy = case()
    healthy["alert_rule_test"] = dashboard_checks
    tests.append(healthy)
    # In contrast to an idle healthy database, missing telemetry must not produce a zero OOM rate.
    oom_query = next(p for p in dashboard["panels"] if p["id"] == 9)["targets"][0]["expr"]
    rules.append({"alert": "ValkeyUnknownIsNotZero", "expr": oom_query})
    tests.append({"interval": "1m", "input_series": [], "alert_rule_test": [
        {"eval_time": "16m", "alertname": "ValkeyUnknownIsNotZero", "exp_alerts": []}]})
    for value in ("0", "1"):
        tests.append({"interval": "1m", "input_series": [{"series": db("redis_up"), "values": value + "+0x50"}],
            "alert_rule_test": [{"eval_time": "16m", "alertname": "ValkeyUnknownIsNotZero",
                "exp_alerts": [{"exp_labels": {}, "exp_annotations": {}}] if value == "1" else []}]})
    image = re.search(r'image: "(victoriametrics/vmalert:[^"]+)"', rendered)[1]
    with tempfile.TemporaryDirectory(prefix="valkey-alerts-") as directory:
        work = Path(directory)
        (work / "rules.json").write_text(json.dumps({"groups": [{"name": "valkey", "rules": rules}]}))
        (work / "tests.json").write_text(json.dumps({"rule_files": ["/work/rules.json"],
            "evaluation_interval": "1m", "tests": tests}))
        subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/work",
                        image.replace("/vmalert:", "/vmalert-tool:"), "unittest",
                        "--disableAlertgroupLabel", "--files=/work/tests.json"], check=True)
    print(f"Valkey: {len(tests)} scenarios and all dashboard queries passed.", flush=True)


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
