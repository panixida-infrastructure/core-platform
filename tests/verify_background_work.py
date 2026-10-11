"""Exercise background-work alerts and panel queries with the deployed vmalert version."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap


def verify(rendered):
    rules = [{"alert": name, "expr": expr, "for": duration}
             for name, expr, duration in re.findall(
                 r'- alert: ((?:Wolverine|Quartz)\w+)\n\s+expr: ([^\n]+)\n\s+for: ([^\n]+)', rendered)]
    assert len(rules) == 10, "Expected ten background-work alerts"
    service = {"service_name": "test-api-development"}
    job = service | {"quartz_job_group": "maintenance", "quartz_job_name": "cleanup"}
    scheduler = service | {"quartz_scheduler_name": "QuartzScheduler"}
    prefix = "wolverine_persistence_"

    def series(metric, values, labels=None):
        dimensions = service if labels is None else labels
        tags = ",".join(f"{key}={json.dumps(value)}" for key, value in dimensions.items())
        return {"series": f"{metric}{{{tags}}}", "values": values}

    def case(inputs, expected=None, times=("4m", "16m", "31m"), selected=None):
        expected = expected or {}
        checks = [{"eval_time": when, "alertname": rule["alert"],
                   "exp_alerts": [{"exp_labels": labels, "exp_annotations": {}}
                                  for labels in expected.get((rule["alert"], when), [])]}
                  for when in times for rule in (rules if selected is None else selected)]
        return {"interval": "1m", "input_series": inputs, "alert_rule_test": checks}

    def fires(name, labels=service, times=("16m", "31m")):
        return {(name, when): [labels] for when in times}

    tests = [case([])]  # Missing packages/schedulers must not generate false alerts.
    for metric, alert in [("wolverine_inbox_count_Messages", "WolverineInboxBacklogHigh"),
                          ("wolverine_outbox_count_Messages", "WolverineOutboxBacklogHigh")]:
        tests += [case([series(metric, "101+0x40")], fires(alert)),
                  case([series(metric, "100+0x40")]),
                  case([series(metric, "60+0x40", service | {"service_instance_id": instance})
                        for instance in ("a", "b")]),  # Shared DB inventory must not be summed.
                  case([series(metric, "101+0x4 0+0x36")])]
    tests += [
        case([series(prefix + "dead_letters", "11+0x40")], fires("WolverineDeadLetterBacklogHigh")),
        case([series(prefix + "dead_letters", "10+0x40")]),
        case([series(prefix + "dead_letters", "1+0x40"),
              series(prefix + "dead_letter_oldest_age_seconds", "4000+0x40")],
             fires("WolverineDeadLetterOldMessages")),
        case([series(prefix + "dead_letters", "0+0x40"),
              series(prefix + "dead_letter_oldest_age_seconds", "4000+0x40")]),
        case([series(prefix + "scheduled_overdue_seconds", "301+0x40")],
             fires("WolverineScheduledMessagesOverdue")),
        case([series("wolverine_scheduled_count_Messages", "10000+0x40"),
              series(prefix + "scheduled_due", "0+0x40"),
              series(prefix + "scheduled_overdue_seconds", "0+0x40")]),
        case([series(prefix + "recurring_overdue_seconds", "301+0x40", service | {"schedule_name": "cleanup"})],
             fires("WolverineRecurringScheduleOverdue", service | {"schedule_name": "cleanup"})),
        case([series(prefix + "recurring_paused", "1+0x40", service | {"schedule_name": "cleanup"})]),
    ]
    for metric, alert, labels, expected_labels in [
        ("wolverine_dead_letter_queue_Messages_total", "WolverineDeadLettersArriving", service, service),
        ("quartz_job_execution_duration_seconds_count", "QuartzJobFailed", job | {"error_type": "Exception"}, job),
        ("quartz_trigger_misfire_total", "QuartzTriggerMisfires", scheduler, scheduler),
    ]:
        tests.append(case([series(metric, "0 1+0x39", labels)], fires(alert, expected_labels, ("4m",))))
        tests.append(case([series(metric, "_ _ 1+0x38", labels)], fires(alert, expected_labels, ("4m", "16m"))))
        tests.append(case([series(metric, "0+0x40", labels)]))
        tests.append(case([series(metric, "100+0x20 0+0x20", labels)], times=("31m",)))
        # Two pods, each increasing: sum counter changes, not the raw inventory.
        tests.append(case([series(metric, "0+1x40", labels | {"service_instance_id": instance})
                           for instance in ("a", "b")], fires(alert, expected_labels, ("4m", "16m", "31m"))))

    histogram = "quartz_job_execution_duration_seconds"
    def histogram_series(new_layout=True, slow=True, instance="new"):
        dimensions = job | {"service_instance_id": instance, "quartz_scheduler_name": "QuartzScheduler",
                            "quartz_scheduler_id": "NON_CLUSTERED"}
        data = [series(histogram + "_count", "0+1x40", dimensions),
                series(histogram + "_sum", "0+90x40" if slow else "0+0.1x40", dimensions)]
        buckets = {"0.001": "0+0x40", "0.1": "0+0x40" if slow else "0+1x40",
                   "60": "0+0x40" if slow else "0+1x40", "120": "0+1x40", "+Inf": "0+1x40"}
        if not new_layout:
            buckets = {"0": "0+0x40", "5": "0+1x40", "+Inf": "0+1x40"}
        data += [series(histogram + "_bucket", values, dimensions | {"le": bound})
                 for bound, values in buckets.items()]
        return data

    tests += [case(histogram_series(), fires("QuartzJobSlow", job, ("31m",))),
              case(histogram_series(slow=False)),
              case(histogram_series(new_layout=False)),
              case(histogram_series(slow=False) + histogram_series(new_layout=False, instance="old"))]

    dashboard = json.loads(re.search(r"  application-telemetry\.json: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1])
    panels = [p for p in dashboard["panels"] if p["id"] >= 100]
    assert len(panels) == 17
    assert len({p['id'] for p in dashboard['panels']}) == len(dashboard['panels']), "Duplicate panel IDs"
    panel_rules = []
    for panel in panels:
        for target in panel.get("targets", []):
            assert target["range"] and not target["instant"], panel["title"]
            assert "vector(0)" not in target["expr"], panel["title"]
            expr = target["expr"].replace("${service:regex}", ".*")
            panel_rules.append({"alert": f"Panel{panel['id']}{target['refId']}", "expr": f"count({expr}) > 0"})
    fixtures = [series(metric, "1+0x40") for metric in [
        "wolverine_inbox_count_Messages", "wolverine_outbox_count_Messages", "wolverine_scheduled_count_Messages",
        prefix + "dead_letters", prefix + "dead_letter_oldest_age_seconds", prefix + "scheduled_due",
        prefix + "scheduled_overdue_seconds"]]
    fixtures += [series(prefix + "recurring_paused", "0+0x40", service | {"schedule_name": "cleanup"}),
                 series(prefix + "recurring_overdue_seconds", "0+0x40", service | {"schedule_name": "cleanup"}),
                 series("quartz_job_execution_active", "0+0x40", job),
                 series("quartz_trigger_acquisition_duration_seconds_count", "0+1x40", scheduler)]
    # Arrival, error and misfire counters deliberately absent: anchored zero fallbacks still render.
    tests.append(case(fixtures + histogram_series(slow=False),
                      {(r["alert"], "4m"): [{}] for r in panel_rules}, times=("4m",), selected=panel_rules))
    image = re.search(r'image: "(victoriametrics/vmalert:[^"]+)"', rendered)[1]
    with tempfile.TemporaryDirectory(prefix="background-work-") as directory:
        work = Path(directory)
        full_rules = re.search(r"  rules.yml: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1]
        (work / "full-rules.yaml").write_text(textwrap.dedent(full_rules))
        subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/work", image,
                        "-dryRun", "-rule=/work/full-rules.yaml"], check=True)
        (work / "rules.json").write_text(json.dumps({"groups": [{"name": "background-work", "rules": rules + panel_rules}]}))
        (work / "tests.json").write_text(json.dumps({"rule_files": ["/work/rules.json"],
            "evaluation_interval": "1m", "tests": tests}))
        subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/work",
                        image.replace("/vmalert:", "/vmalert-tool:"), "unittest",
                        "--disableAlertgroupLabel", "--files=/work/tests.json"], check=True)
    print(f"Background work: {len(tests)} scenarios and {len(panel_rules)} dashboard queries passed.")


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
