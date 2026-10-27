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
    def duration_series(duration=90, executions=1, instance="a"):
        dimensions = job | {"service_instance_id": instance, "quartz_scheduler_name": "QuartzScheduler",
                            "quartz_scheduler_id": "NON_CLUSTERED"}
        return [series(histogram + "_count", f"0+{executions}x40", dimensions),
                series(histogram + "_sum", f"0+{duration * executions}x40", dimensions)]

    weighted_durations = duration_series(duration=200) + duration_series(duration=10, executions=9, instance="b")
    tests += [case(duration_series(), fires("QuartzJobSlow", job, ("31m",))),
              case(duration_series(duration=60)),
              case(duration_series(duration=0.1)),
              case(duration_series(executions=0)),
              case(weighted_durations),
              case(duration_series() + duration_series(instance="b"), fires("QuartzJobSlow", job, ("31m",))),
              case([series(histogram + "_count", "0+0x9 1+0x9 2+0x9 3+0x10", job),
                    series(histogram + "_sum", "0+0x9 900+0x9 1800+0x9 2700+0x10", job)]),
              case([series(histogram + "_count", "0+1x20 0+1x19", job),
                    series(histogram + "_sum", "0+90x20 0+90x19", job)], fires("QuartzJobSlow", job, ("31m",)))]

    dashboard = json.loads(re.search(r"  application-telemetry\.json: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1])
    panels = [p for p in dashboard["panels"] if 100 <= p["id"] < 120]
    assert len(panels) == 17
    assert len({p['id'] for p in dashboard['panels']}) == len(dashboard['panels']), "Duplicate panel IDs"
    panel_rules = []
    for panel in panels:
        for target in panel.get("targets", []):
            assert target["range"] and not target["instant"], panel["title"]
            assert "vector(0)" not in target["expr"], panel["title"]
            expr = target["expr"].replace("${service:regex}", ".*").replace("${environment:regex}", ".*").replace("${instance:regex}", ".*")
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
    tests.append(case(fixtures + duration_series(duration=0.1),
                      {(r["alert"], "4m"): [{}] for r in panel_rules}, times=("4m",), selected=panel_rules))
    duration_panel = next(panel for panel in panels if panel["id"] == 113)
    assert len(duration_panel["targets"]) == 1
    mean_expr = duration_panel["targets"][0]["expr"].replace("${service:regex}", ".*").replace("${environment:regex}", ".*").replace("${instance:regex}", ".*")
    mean_rule = {"alert": "QuartzMeanDurationValue", "expr": f"abs(({mean_expr}) - 29) < 0.000001"}
    panel_rules.append(mean_rule)
    tests.append(case(weighted_durations, fires(mean_rule["alert"], job, ("4m",)),
                      times=("4m",), selected=[mean_rule]))
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
