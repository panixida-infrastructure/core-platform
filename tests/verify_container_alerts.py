"""Exercise container lifecycle alerts, routing and graph queries with vmalert-tool."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def verify(rendered):
    names = ("KubernetesContainerRestartsHigh", "KubernetesContainerCrashLoopBackOff",
             "KubernetesContainerTerminatedAbnormally", "KubernetesContainerStateMonitoringDown")
    rules = []
    for name in names:
        block = re.search(r"- alert: " + name + r"\n(.*?)(?=\n          - alert:)", rendered, re.S)[1]
        labels = re.search(r"            labels:\n(.*?)            annotations:", block, re.S)[1]
        rules.append({"alert": name, "expr": re.search(r"expr: (.+)", block)[1],
                      "for": re.search(r"for: (.+)", block)[1],
                      "annotations": {"summary": re.search(r"summary: (.+)", block)[1]},
                      "labels": {k: v.strip("'") for k, v in re.findall(r"              (\w+): (.+)", labels)}})

    epoch = 946684800  # vmalert-tool's fixture epoch.
    prefix = "kube_pod_container_status_"
    base = {"namespace": "dotnet-template-production", "pod": "api-123", "container": "api", "uid": "pod-a"}
    definitions = {"restarts_total": "0+0x25", 'waiting_reason|reason=CrashLoopBackOff': "0+0x25"}
    tests = []

    def summary(name, labels):
        if name == names[3]:
            return "Kubernetes container state monitoring is unavailable."
        who = f"Container {labels['namespace']}/{labels['pod']} ({labels['container']})"
        suffix = {names[0]: "is restarting repeatedly.", names[1]: "is stuck in CrashLoopBackOff.",
                  names[2]: f"terminated abnormally ({labels.get('reason', '')})."}
        return f"{who} {suffix[name]}"

    def add(changes=None, expected=None, times=("2m", "6m", "12m"), dimensions=None,
            init=False, deleted=False, succeeded=False, missing=False, up="1+0x25", extra=None):
        dimensions = dimensions or base
        current_prefix = prefix.replace("container_", "init_container_") if init else prefix
        series = []
        def sample(metric, labels, values):
            serialized = ",".join(f"{k}={json.dumps(v)}" for k, v in labels.items())
            series.append({"series": f"{metric}{{{serialized}}}", "values": values})
        if not missing:
            sample("up", {"service_name": "kube-state-metrics"}, up)
            for key, values in (definitions | (changes or {})).items():
                metric, _, attributes = key.partition("|")
                labels = dimensions | dict(x.split("=", 1) for x in attributes.split(",") if x)
                sample(current_prefix + metric, labels, values)
            sample("kube_pod_info", {k: v for k, v in dimensions.items() if k != "container"} | {"node": "worker-1"}, "1+0x25")
            if init:
                sample(prefix + "restarts_total", dimensions | {"container": "main"}, "0+0x25")
            if deleted:
                sample("kube_pod_deletion_timestamp", {k: v for k, v in dimensions.items() if k != "container"}, f"{epoch}+0x25")
            if succeeded:
                sample("kube_pod_status_phase", {k: v for k, v in dimensions.items() if k != "container"} | {"phase": "Succeeded"}, "1+0x25")
        series.extend(extra or [])
        checks = [{"eval_time": when, "alertname": name,
                   "exp_alerts": [{"exp_labels": labels, "exp_annotations": {"summary": summary(name, labels)}}
                                  for labels in (expected or {}).get((name, when), [])]}
                  for when in times for name in names]
        tests.append({"interval": "1m", "input_series": series, "alert_rule_test": checks})

    def fires(name, times, dimensions=None, **additional):
        labels = (dimensions or base) | additional
        namespace = labels["namespace"]
        owner = ("dotnet-template" if namespace.startswith("dotnet-template-") else
                 "tactical-heroes" if namespace.startswith("tactical-heroes-") else
                 "observability" if namespace == "observability" else "core-platform")
        labels |= {"alert_owner": owner, "environment": "development" if namespace.endswith("-development") else "production",
                   "severity": "critical" if name == names[1] else "warning"}
        return {(name, when): [labels] for when in times}

    add()  # Healthy containers.
    add({"restarts_total": "7+0x25"})  # Cold-start inventory must not page about old restarts.
    add({"restarts_total": "7 7 0+0x23"})  # Kubelet counter reset.
    add({"restarts_total": "0 1 2+0x23"})  # Two restarts stay below the threshold.
    add({"restarts_total": "0 0 1 2 3+0x21"}, fires(names[0], ("6m", "12m")))
    add({'waiting_reason|reason=CrashLoopBackOff': "1+0x25"}, fires(names[1], ("12m",)))
    add({'waiting_reason|reason=CrashLoopBackOff': "1 0+0x24"})  # A transient backoff clears before the hold time.

    recent = {'last_terminated_reason|reason=Error': "1+0x25", "last_terminated_exitcode": "1+0x25",
              "last_terminated_timestamp": f"{epoch}+0x25"}
    add(recent, fires(names[2], ("2m", "6m"), reason="Error"))  # Resolves after the ten-minute window.
    oom = {'last_terminated_reason|reason=OOMKilled': "1+0x25", "last_terminated_exitcode": "137+0x25",
           "last_terminated_timestamp": f"{epoch}+0x25"}
    add(oom, fires(names[2], ("2m", "6m"), reason="OOMKilled"))
    add(oom | {"last_terminated_timestamp": f"{epoch - 3600}+0x25"})  # Old OOM does not alert again.
    add({'last_terminated_reason|reason=Completed': "1+0x25", "last_terminated_exitcode": "0+0x25",
         "last_terminated_timestamp": f"{epoch}+0x25", 'terminated_reason|reason=Completed': "1+0x25"})
    add({'terminated_reason|reason=Error': "1+0x25"}, fires(names[2], ("2m", "6m", "12m"), reason="Error"))
    add(recent, fires(names[2], ("2m", "6m"), reason="Error"), init=True)
    add(recent | {"restarts_total": "0 0 1 2 3+0x21", 'waiting_reason|reason=CrashLoopBackOff': "1+0x25"}, deleted=True)
    add(recent | {"restarts_total": "0 0 1 2 3+0x21"}, succeeded=True)
    # Replacing a pod with another UID cannot carry over its restart history.
    add(extra=[{"series": prefix + 'restarts_total{namespace="dotnet-template-production",pod="api-123",container="api",uid="old-pod"}',
                "values": "7 7 stale _x22"}])
    for namespace in ("tactical-heroes-admin-development", "observability", "kube-system"):
        dims = base | {"namespace": namespace}
        add(recent, fires(names[2], ("2m", "6m"), dims, reason="Error"), dimensions=dims)
    unavailable = {(names[3], when): [{"alert_owner": "observability", "environment": "production", "severity": "warning"}]
                   for when in ("6m", "12m")}
    add(up="0+0x25", expected=unavailable)
    add(missing=True, expected=unavailable)

    dashboard = json.loads(re.search(r"  workloads\.json: \|\n((?:    [^\n]*\n|\n)+)", rendered)[1])
    # Execute every new graph query against real fixtures, with both all and exact selectors.
    fixture = tests[0]["input_series"]
    graph_checks = []
    for panel in dashboard["panels"]:
        if panel["id"] not in (18, 19, 20, 21):
            continue
        for target in panel["targets"]:
            for selection, replacements in (("All", {"node": ".*", "namespace": ".*", "pod": ".*", "container": ".*"}),
                                            ("Exact", base | {"node": "worker-1"})):
                expr = re.sub(r"\$\{(\w+):(?:regex|pipe)\}", lambda m: replacements[m[1]], target["expr"])
                name = f"ContainerDashboard{panel['id']}{target['refId']}{selection}"
                rules.append({"alert": name, "expr": f"count({expr}) > 0"})
                graph_checks.append({"eval_time": "6m", "alertname": name,
                                     "exp_alerts": [{"exp_labels": {}, "exp_annotations": {}}]})
    assert len(graph_checks) == 8
    tests.append({"interval": "1m", "input_series": fixture, "alert_rule_test": graph_checks})
    image = re.search(r'image: "(victoriametrics/vmalert:[^"]+)"', rendered)[1].replace("/vmalert:", "/vmalert-tool:")
    with tempfile.TemporaryDirectory(prefix="container-alerts-") as directory:
        work = Path(directory)
        (work / "rules.json").write_text(json.dumps({"groups": [{"name": "containers", "rules": rules}]}), encoding="utf-8")
        (work / "tests.json").write_text(json.dumps({"rule_files": ["/work/rules.json"], "evaluation_interval": "1m", "tests": tests}), encoding="utf-8")
        subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/work", image, "unittest",
                        "--disableAlertgroupLabel", "--files=/work/tests.json"], check=True)
    print(f"Container alerts: {len(tests)} scenarios, including routing and filtered dashboard queries, passed.", flush=True)


if __name__ == "__main__":
    verify(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
