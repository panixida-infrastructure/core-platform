"""Exercise the rendered backup alert expressions using the deployed vmalert version."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def verify(rendered):
    names = ("TimewebPostgresBackupMonitoringStalled", "TimewebPostgresAutomaticBackupsDisabled",
             "TimewebPostgresAutomaticBackupStale", "TimewebPostgresNoSuccessfulBackup",
             "TimewebPostgresBackupFailed")
    rules = []
    for name in names:
        match = re.search(r'- alert: ' + name + r'\n\s+expr: ([^\n]+)\n\s+for: ([^\n]+)', rendered)
        assert match is not None, name
        rules.append({"alert": name, "expr": match[1], "for": match[2]})

    labels = {"db_cluster_id": "4169562", "db_cluster": "postgres"}
    prefix = "timeweb_postgres_backup_"
    epoch = 946684800  # vmalert-tool fixture epoch: 2000-01-01 UTC.
    definitions = {
        "poll_last_success_timestamp_seconds": f"{epoch}+60x90",
        "poll_success": "1+0x90", "auto_enabled": "1+0x90", "retention_copies": "1+0x90", "max_age_seconds": "129600+0x90",
        'count|backup_type=auto,status=done': "1+0x90",
        'count|backup_type=manual,status=done': "1+0x90",
        'last_success_timestamp_seconds|backup_type=auto': f"{epoch - 86400}+0x90",
        'last_success_timestamp_seconds|backup_type=manual': f"{epoch - 3600}+0x90",
        'last_attempt_failed|backup_type=auto': "0+0x90",
        'last_attempt_failed|backup_type=manual': "0+0x90",
    }

    def case(changes, expected, evaluations=("4m", "16m", "31m"), missing=False):
        series = []
        for key, values in ({} if missing else definitions | changes).items():
            metric, _, extra = key.partition("|")
            dimensions = labels | dict(part.split("=", 1) for part in extra.split(",") if part)
            serialized = ",".join(f"{key}={json.dumps(value)}" for key, value in dimensions.items())
            series.append({"series": f"{prefix}{metric}{{{serialized}}}", "values": values})
        checks = []
        for when in evaluations:
            for rule in rules:
                matches = expected.get((rule["alert"], when), [])
                checks.append({"eval_time": when, "alertname": rule["alert"], "exp_alerts": [
                    {"exp_labels": item, "exp_annotations": {}} for item in matches]})
        return {"interval": "1m", "input_series": series, "alert_rule_test": checks}

    def fires(name, dimensions, *times):
        return {(name, when): [dimensions] for when in times}

    stale = 'last_success_timestamp_seconds|backup_type=auto'
    auto_count, manual_count = 'count|backup_type=auto,status=done', 'count|backup_type=manual,status=done'
    tests = [
        case({}, {}),
        # A recent manual copy must not hide a stale or absent automatic copy.
        case({stale: f"{epoch - 40 * 3600}+0x90"},
             fires("TimewebPostgresAutomaticBackupStale", labels | {"backup_type": "auto"}, "31m")),
        case({stale: "0+0x90", auto_count: "0+0x90"},
             fires("TimewebPostgresAutomaticBackupStale", labels | {"backup_type": "auto"}, "31m")),
        # In-progress copies are not recoverable, and the critical no-copy rule suppresses stale-auto noise.
        case({auto_count: "0+0x90", manual_count: "0+0x90", stale: "0+0x90",
              'count|backup_type=auto,status=create': "1+0x90"},
             fires("TimewebPostgresNoSuccessfulBackup", labels, "16m", "31m")),
        case({"auto_enabled": "0+0x90", stale: "0+0x90", auto_count: "0+0x90"},
             fires("TimewebPostgresAutomaticBackupsDisabled", labels, "16m", "31m")),
        case({'last_attempt_failed|backup_type=auto': "1+0x90"},
             fires("TimewebPostgresBackupFailed", labels | {"backup_type": "auto"}, "16m", "31m")),
        # API failures must not be reported as disabled schedules or missing copies.
        case({"poll_success": "0+0x90", "auto_enabled": "0+0x90",
              auto_count: "0+0x90", manual_count: "0+0x90",
              "poll_last_success_timestamp_seconds": f"{epoch - 7200}+0x90"},
             fires("TimewebPostgresBackupMonitoringStalled", labels, "16m", "31m")),
        case({"poll_success": "0+0x90"}, {}),
        # Also detect a stuck poll loop whose HTTP metrics server still responds.
        case({"auto_enabled": "0+0x90", "poll_last_success_timestamp_seconds": f"{epoch - 7200}+0x90"},
             fires("TimewebPostgresBackupMonitoringStalled", labels, "16m", "31m")),
        case({}, fires("TimewebPostgresBackupMonitoringStalled", {}, "16m", "31m"), missing=True),
        case({stale: f"{epoch - 40 * 3600}+0x14 {epoch}+0x75"}, {}),
    ]
    # Execute every dashboard query too: valid JSON alone cannot catch MetricsQL syntax errors.
    dashboard_json = re.search(r"  postgresql-backups\.json: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1]
    dashboard = json.loads(dashboard_json)
    dashboard_checks = {}
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            name = f"BackupDashboardPanel{panel['id']}{target['refId']}"
            rules.append({"alert": name, "expr": f"count({target['expr']}) > 0"})
            dashboard_checks.update(fires(name, {}, "4m"))
    tests.append(case({}, dashboard_checks, evaluations=("4m",)))
    image = re.search(r'image: "(victoriametrics/vmalert:[^"]+)"', rendered)[1]
    with tempfile.TemporaryDirectory(prefix="backup-alerts-") as directory:
        work = Path(directory)
        (work / "rules.json").write_text(json.dumps({"groups": [{"name": "backups", "rules": rules}]}))
        (work / "tests.json").write_text(json.dumps({"rule_files": ["/work/rules.json"],
            "evaluation_interval": "1m", "tests": tests}))
        subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/work",
                        image.replace("/vmalert:", "/vmalert-tool:"), "unittest",
                        "--disableAlertgroupLabel", "--files=/work/tests.json"], check=True)
    print(f"Backup alerts: {len(tests)} scenarios passed with the deployed vmalert version.", flush=True)


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
