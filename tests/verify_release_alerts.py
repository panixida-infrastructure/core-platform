"""Run release rules and dashboard queries with the deployed vmalert version."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap


def verify(rendered):
    names = ("InfrastructureUpdateAvailable", "InfrastructureReleaseCheckFailed", "InfrastructureReleaseMonitorDown")
    rules = []
    for name in names:
        match = re.search(r'- alert: ' + name + r'\n\s+expr: ([^\n]+)\n\s+for: ([^\n]+)', rendered)
        assert match, name
        rules.append({"alert": name, "expr": match[1], "for": match[2]})
    prefix = "infrastructure_release_"
    epoch = 946684800
    labels = {"component": "Grafana", "current_version": "13.2.3", "latest_version": "13.3.0",
              "release_url": "https://github.com/grafana/grafana/releases/tag/v13.3.0", "note": "Review changelog."}
    dimensions = ",".join(f'{key}={json.dumps(value)}' for key, value in labels.items())
    definitions = {
        "inventory_success": "1+0x90", "state_success": "1+0x90",
        "inventory_last_success_timestamp_seconds": f"{epoch}+60x90",
        f'info{{{dimensions},status="update_available"}}': "1+0x90",
        'last_success_timestamp_seconds{component="Grafana"}': f"{epoch}+0x90",
    }

    def fires(name, dimensions, *times):
        return {(name, when): [{"exp_labels": dimensions, "exp_annotations": {}}] for when in times}

    def case(changes, expected, times=("2m", "11m", "31m", "65m"), missing=False):
        return {"interval": "1m", "input_series": [
            {"series": prefix + key, "values": value} for key, value in ({} if missing else definitions | changes).items()],
            "alert_rule_test": [{"eval_time": when, "alertname": rule["alert"],
                                 "exp_alerts": expected.get((rule["alert"], when), [])}
                                for when in times for rule in rules]}

    event = f"new_version{{{dimensions}}}"
    tests = [
        case({}, {}),
        case({event: "1+0x59 0+0x31"}, fires(names[0], labels, "2m", "11m", "31m")),
        case({event: "1+0x90", "inventory_success": "0+0x90"}, fires(names[2], {}, "11m", "31m", "65m")),
        case({"state_success": "0+0x90"}, fires(names[2], {}, "11m", "31m", "65m")),
        case({event: "1+0x90", "inventory_last_success_timestamp_seconds": f"{epoch-3600}+0x90"},
             fires(names[2], {}, "11m", "31m", "65m")),
        case({}, fires(names[2], {}, "11m", "31m", "65m"), missing=True),
    ]
    for status in ("check_failed", "inventory_failed", "untracked"):
        tests.append(case({f'info{{component="Broken",status="{status}"}}': "1+0x90"},
                          fires(names[1], {"component": "Broken", "status": status}, "31m", "65m")))
    tests.append(case({'info{component="Transient",status="check_failed"}': "1+0x10 0+0x80"}, {}))
    for status in ("current", "not_installed", "provider_managed", "own_build"):
        tests.append(case({f'info{{component="Other",status="{status}"}}': "1+0x90"}, {}))

    dashboard = json.loads(re.search(r"  core-platform-overview\.json: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1])
    expected = {}
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            if prefix in target["expr"]:
                name = f"ReleasePanel{panel['id']}"
                rules.append({"alert": name, "expr": f"count({target['expr']}) > 0"})
                expected.update(fires(name, {}, "2m"))
    assert len(expected) == 5
    tests.append(case({}, expected, times=("2m",)))

    # Route must be separate from incident notifications and never emit resolved updates.
    route = re.search(r'- receiver: telegram-infrastructure-updates\n(.*?)(?=\n        - receiver:)', rendered, re.S)[1]
    assert 'group_by: ["alertname", "component", "latest_version"]' in route
    assert 'repeat_interval: 24h' in route
    receiver = re.search(r'- name: telegram-infrastructure-updates\n(.*?)(?=\n      - name:)', rendered, re.S)[1]
    assert 'message_thread_id: 960' in receiver and 'send_resolved: false' in receiver
    assert 'template "telegram.panixida.release"' in receiver

    image = re.search(r'image: "(victoriametrics/vmalert:[^"]+)"', rendered)[1]
    with tempfile.TemporaryDirectory(prefix="release-alerts-") as directory:
        work = Path(directory)
        work.chmod(0o755)
        (work / "rules.json").write_text(json.dumps({"groups": [{"name": "releases", "rules": rules}]}), encoding="utf-8")
        (work / "tests.json").write_text(json.dumps({"rule_files": ["/work/rules.json"], "evaluation_interval": "1m", "tests": tests}), encoding="utf-8")
        subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{work}:/work",
                        image.replace("/vmalert:", "/vmalert-tool:"), "unittest", "--disableAlertgroupLabel", "--files=/work/tests.json"], check=True)

        # Parse the actual Alertmanager configuration using harmless local credentials.
        config = textwrap.dedent(re.search(r"  alertmanager\.yml: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1])
        config = re.sub(r"/etc/[^\s]+/(?:telegram-bot-token|webhook-token)", "/work/token", config)
        config = config.replace("/etc/alertmanager/telegram.tmpl", "/work/telegram.tmpl")
        (work / "alertmanager.yml").write_text(config, encoding="utf-8")
        (work / "token").write_text("test-token", encoding="utf-8")
        template = textwrap.dedent(re.search(r"  telegram\.tmpl: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1])
        (work / "telegram.tmpl").write_text(template, encoding="utf-8")
        am_image = re.search(r'image: "(prom/alertmanager:[^"]+)"', rendered)[1]
        command = ["docker", "run", "--rm", "--network", "none", "-v", f"{work}:/work", "--entrypoint", "/bin/amtool", am_image]
        subprocess.run(command + ["check-config", "/work/alertmanager.yml"], check=True)
        data = {"receiver":"telegram-infrastructure-updates", "status":"firing", "alerts":[
            {"status":"firing", "labels": labels | {"component":"Grafana <test>"},
             "annotations":{"dashboard_url":"https://grafana.panixida.ru/d/core-platform-overview/core-platform-overview?viewPanel=10"},
             "startsAt":"2026-10-11T00:00:00Z", "endsAt":"2026-10-11T01:00:00Z"}]}
        (work / "data.json").write_text(json.dumps(data), encoding="utf-8")
        result = subprocess.run(command + ["template", "render", "--template.glob=/work/telegram.tmpl", "--template.type=html",
                                '--template.text={{ template "telegram.panixida.release" . }}', "--template.data=/work/data.json"],
                                check=True, capture_output=True, encoding="utf-8")
        assert "Grafana &lt;test&gt;" in result.stdout
        assert "13.2.3" in result.stdout and "13.3.0" in result.stdout and labels["release_url"] in result.stdout
        assert "Доступно обновление" in result.stdout and "resolved" not in result.stdout
    print(f"Release monitoring: {len(tests)} vmalert scenarios, five panel queries, route and Telegram template passed.")


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
