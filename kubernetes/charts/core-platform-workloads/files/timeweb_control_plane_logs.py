"""Forward managed control-plane logs to the existing OpenTelemetry collector."""

import copy
import datetime as dt
import hashlib
import http.server
import json
import os
import re
import signal
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


FIELDS = re.compile(r'(\w+)=("(?:\\.|[^"\\])*"|[^\s]+)')
LEVELS = {"trace": (1, "Trace"), "debug": (5, "Debug"), "info": (9, "Information"),
          "warn": (13, "Warning"), "warning": (13, "Warning"),
          "error": (17, "Error"), "fatal": (21, "Critical"), "panic": (21, "Critical")}
SCOPES = ("k0scontroller.service", "cloud-provider-timeweb-cloud.service",
          "tw-kube-healer.service", "twcp.service")
OVERLAP_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 20
CHECKPOINT_SIZE_LIMIT = 768 * 1024
DIAGNOSTIC_CLIENT_IPS = frozenset(filter(None, os.environ.get("DIAGNOSTIC_CLIENT_IPS", "").split(",")))
CLIENT_STREAM_RESET = re.compile(
    r'^E\d{4}\s[^\]]+\supgradeaware\.go:\d+\] '
    r'"Error proxying data from client to backend" '
    r'err="read tcp [^ ]+:6443->(?P<peer>(?:\d{1,3}\.){3}\d{1,3}):\d+: '
    r'read: connection reset by peer"$')
CLIENT_STREAM_RESET_ATTRIBUTE = "k8s.apiserver.diagnostic_stream_reset"


def attribute(key, value):
    return {"key": key, "value": {"stringValue": str(value)}}


def normalize(line, node, observed, scope="k0scontroller.service"):
    fields = {}
    for key, value in FIELDS.findall(line):
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                value = value[1:-1]
        fields[key] = value
    message = fields.get("msg", line)
    level = fields.get("level", "info").lower()
    glog = re.match(r'^([IWEF])\d{4}\s', message)
    if glog:
        level = {"I": "info", "W": "warn", "E": "error", "F": "fatal"}[glog[1]]
    timestamp = None
    try:
        timestamp = dt.datetime.fromisoformat(fields["time"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
        timestamp = timestamp.timestamp()
    except (KeyError, ValueError):
        pass
    # The Timeweb UTC rendering of bare klog lines omits the year.
    if timestamp is None:
        match = re.match(
            r'^[IWEF](\d{2})(\d{2}) (\d{2}:\d{2}:\d{2})'
            r'(?: GMT(?P<zone>[+-]\d{2}:?\d{2})? (?P<gmt_micros>\d{6})|\.(?P<micros>\d{6}))', message)
        if match:
            reference = dt.datetime.fromtimestamp(observed, dt.timezone.utc)
            candidates = []
            for year in (reference.year - 1, reference.year, reference.year + 1):
                try:
                    value = dt.datetime.fromisoformat(
                        f"{year}-{match[1]}-{match[2]}T{match[3]}."
                        f"{match['gmt_micros'] or match['micros']}{match['zone'] or '+00:00'}")
                    candidates.append(value.timestamp())
                except ValueError:
                    pass
            if candidates:
                timestamp = min(candidates, key=lambda value: abs(value - observed))
    severity, text = LEVELS.get(level, LEVELS["info"])
    attrs = [attribute("alert_owner", "core-platform"), attribute("log.source", "timeweb-api"),
             attribute("timeweb.log.scope", scope)]
    component = fields.get("component", scope.removesuffix(".service"))
    reset = CLIENT_STREAM_RESET.fullmatch(message)
    if (level == "error" and component == "kube-apiserver" and scope == "k0scontroller.service"
            and reset and reset["peer"] in DIAGNOSTIC_CLIENT_IPS):
        # Only confirmed operator peers get lower-priority alerts; retain the source error.
        attrs.extend([attribute("log.original.severity_text", text),
                      attribute(CLIENT_STREAM_RESET_ATTRIBUTE, "true")])
        severity, text = LEVELS["warn"]
    if timestamp is None:
        attrs.append(attribute("timeweb.timestamp.missing", "true"))
    record = {"observedTimeUnixNano": str(int(observed * 1_000_000_000)),
              "severityNumber": severity, "severityText": text,
              "body": {"stringValue": message}, "attributes": attrs}
    if timestamp is not None:
        record["timeUnixNano"] = str(int(timestamp * 1_000_000_000))
    return component, timestamp, record


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionFailure("unexpected_redirect")


class CollectionFailure(RuntimeError):
    def __init__(self, reason):
        super().__init__("Control-plane log collection failed")
        self.fields = {"error_type": type(self).__name__, "failure_reason": reason}


class RequestFailure(RuntimeError):
    def __init__(self, error, operation, attempt, elapsed, scope):
        super().__init__("Control-plane HTTP request failed")
        self.fields = {"error_type": type(error).__name__,
                       "http_status": getattr(error, "code", None),
                       "operation": operation, "attempt": attempt,
                       "elapsed_seconds": round(elapsed, 3),
                       "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS}
        if scope is not None:
            self.fields["timeweb_log_scope"] = scope
        if isinstance(error, urllib.error.URLError):
            self.fields["reason_type"] = type(error.reason).__name__
        if isinstance(error, CollectionFailure):
            self.fields["failure_reason"] = error.fields["failure_reason"]


class Client:
    def __init__(self, cluster, namespace, token_file):
        self.cluster = cluster
        self.namespace = namespace
        self.token_file = token_file
        self.public = urllib.request.build_opener(NoRedirect())
        context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        self.kube = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
        self.state_url = ("https://kubernetes.default.svc/api/v1/namespaces/" + namespace
                          + "/configmaps/timeweb-control-plane-log-state")

    @staticmethod
    def request(opener, url, headers=None, body=None, method=None, *, operation, scope=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        # Retry reads only: an export or checkpoint may have succeeded before timing out.
        attempts = 3 if req.get_method() == "GET" else 1
        started = time.monotonic()
        for attempt in range(1, attempts + 1):
            try:
                with opener.open(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                    if len(raw) > 8 * 1024 * 1024:
                        raise CollectionFailure("response_size_limit")
                    return json.loads(raw) if raw else {}
            except Exception as error:
                failure = RequestFailure(error, operation, attempt,
                                         time.monotonic() - started, scope)
                reason = error.reason if isinstance(error, urllib.error.URLError) else error
                if not isinstance(reason, TimeoutError) or attempt == attempts:
                    raise failure from error
                # Only allowlisted metadata is logged; exception messages can contain secrets.
                print(json.dumps({"level": "warning", "message": "Control-plane HTTP read timed out; retrying",
                                  **failure.fields, "retry_in_seconds": attempt}), flush=True)
                time.sleep(attempt)

    def timeweb(self, path, query=None):
        with open(self.token_file, encoding="utf-8") as source:
            token = source.read().strip()
        url = "https://api.timeweb.cloud/api/v1/k8s/clusters/" + str(self.cluster) + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return self.request(self.public, url, {"Authorization": "Bearer " + token},
                            operation="timeweb" + path, scope=(query or {}).get("scope"))

    def kube_headers(self):
        with open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8") as source:
            token = source.read().strip()
        return {"Authorization": "Bearer " + token, "Content-Type": "application/merge-patch+json"}

    def load_state(self):
        response = self.request(self.kube, self.state_url, self.kube_headers(),
                                operation="kubernetes/load-state")
        return json.loads(response.get("data", {}).get("state.json", "{}"))

    def save_state(self, state):
        self.request(self.kube, self.state_url, self.kube_headers(),
                     {"data": {"state.json": json.dumps(state)}}, "PATCH",
                     operation="kubernetes/save-state")

    def export(self, node, records):
        groups = {}
        for component, _, record in records:
            groups.setdefault(component, []).append(record)
        resources = []
        for component, logs in groups.items():
            attrs = {"service.name": component, "platform.name": "core-platform",
                     "deployment.environment.name": "production", "k8s.cluster.name": "core-platform",
                     "host.name": "master-" + node["node_ip"], "cloud.provider": "timeweb",
                     "timeweb.node.id": node["id"], "node.role": "control-plane"}
            resources.append({"resource": {"attributes": [attribute(k, v) for k, v in attrs.items()]},
                              "scopeLogs": [{"scope": {"name": "timeweb-control-plane-logs"},
                                             "logRecords": logs}]})
        if not resources:
            return
        response = self.request(self.public, os.environ["OTLP_LOGS_ENDPOINT"],
                                {"Content-Type": "application/json"}, {"resourceLogs": resources},
                                operation="otel/export-logs")
        partial = response.get("partialSuccess", {})
        if int(partial.get("rejectedLogRecords", 0)):
            raise CollectionFailure("otlp_rejected_records")


def collect_node(client, node, state, now, scope="k0scontroller.service", page_limit=20, metrics=None):
    """Export/checkpoint each page; advance the watermark only after the whole scan."""
    key = str(node["id"]) + ":" + scope
    previous = state.get(key, {})
    scan = copy.deepcopy(previous.get("scan", {
        "observed": now, "cutoff": previous.get("watermark", now - 180) - OVERLAP_SECONDS,
        "watermark": previous.get("watermark", now - 300), "seen": {}, "counts": {}, "cursor": None}))
    known = previous.get("seen", {})
    exported = 0
    for _ in range(page_limit):
        # Keep the last acknowledged state immutable until this page is confirmed.
        scan = copy.deepcopy(scan)
        query = {"node_ids": node["id"], "limit": 500, "period": "10080m",
                 "scope": scope, "timezone": "UTC"}
        if scan["cursor"]:
            query["cursor"] = scan["cursor"]
        try:
            page = client.timeweb("/logs", query)
        except RequestFailure as error:
            if scan["cursor"] and error.fields["http_status"] == 400:
                # Expired cursors restart from the original cutoff, never from the head.
                checkpoint = {"watermark": scan["cutoff"] + OVERLAP_SECONDS, "seen": known}
                updated = dict(state, **{key: checkpoint})
                client.save_state(updated)
                state.update(updated)
                raise CollectionFailure("cursor_rejected_scan_restarted") from error
            raise
        if (not isinstance(page, dict) or not isinstance(page.get("k8s_logs"), list)
                or not isinstance(page.get("meta"), dict) or "next_cursor" not in page["meta"]):
            raise CollectionFailure("invalid_log_response")
        lines = page["k8s_logs"]
        pending = []
        page_is_older = True
        for line in lines:
            fingerprint = hashlib.sha256(line.encode()).hexdigest()
            record = normalize(line, node, scan["observed"], scope)
            timestamp = record[1]
            if timestamp is None:
                # A new provider format must not silently turn old history into new logs.
                raise CollectionFailure("timestamp_missing")
            if timestamp < scan["cutoff"]:
                continue
            page_is_older = False
            scan["watermark"] = max(scan["watermark"], timestamp)
            if timestamp >= scan["watermark"] - OVERLAP_SECONDS:
                entry = scan["seen"].setdefault(fingerprint, {"time": timestamp, "count": 0})
                entry["count"] += 1
            if fingerprint in known:
                scan["counts"][fingerprint] = scan["counts"].get(fingerprint, 0) + 1
                if scan["counts"][fingerprint] <= known[fingerprint]["count"]:
                    continue
            pending.append(record)
        next_cursor = page.get("meta", {}).get("next_cursor")
        complete = page_is_older or not next_cursor
        if not complete and next_cursor == scan["cursor"]:
            raise CollectionFailure("non_advancing_cursor")
        scan["cursor"] = next_cursor
        scan["seen"] = {k: v for k, v in scan["seen"].items()
                        if v["time"] >= scan["watermark"] - OVERLAP_SECONDS}
        checkpoint = {"watermark": scan["watermark"], "seen": scan["seen"]} if complete else {
            **previous, "scan": scan}
        updated = dict(state, **{key: checkpoint})
        # Leave room for ConfigMap metadata and JSON string escaping under its 1 MiB limit.
        if len(json.dumps(updated).encode()) > CHECKPOINT_SIZE_LIMIT:
            raise CollectionFailure("checkpoint_size_limit")
        if pending:
            client.export(node, list(reversed(pending)))
        if checkpoint != state.get(key):
            # An ambiguous failure can replay this page, but can never skip unconfirmed logs.
            client.save_state(updated)
            state.update(updated)
        exported += len(pending)
        if metrics is not None:
            metrics.records += len(pending)
            metrics.diagnostic_stream_resets += sum(
                any(attr["key"] == CLIENT_STREAM_RESET_ATTRIBUTE for attr in record[2]["attributes"])
                for record in pending)
        if complete:
            return exported
    return exported


class Metrics:
    last_success = 0
    errors = 0
    records = 0
    diagnostic_stream_resets = 0
    pending_scans = 0


def report_failure(error, metrics, **context):
    metrics.errors += 1
    # Exception text, request headers and provider response bodies can contain secrets.
    fields = error.fields if isinstance(error, (RequestFailure, CollectionFailure)) else {
        "error_type": type(error).__name__, "http_status": getattr(error, "code", None)}
    print(json.dumps({"level": "error", "message": "Control-plane log collection failed",
                      **fields, **context}), flush=True)


def collect_cycle(client, metrics):
    state = client.load_state()
    nodes = client.timeweb("/master-nodes").get("nodes", [])
    masters = [node for node in nodes if node.get("type") == "master"]
    if not masters:
        raise CollectionFailure("no_master_nodes")
    failed = False
    pending = 0
    for node in masters:
        for scope in SCOPES:
            try:
                collect_node(client, node, state, time.time(), scope, metrics=metrics)
            except Exception as error:
                failed = True
                report_failure(error, metrics, timeweb_node_id=node["id"], timeweb_log_scope=scope)
            pending += "scan" in state.get(str(node["id"]) + ":" + scope, {})
    metrics.pending_scans = pending
    if not failed and not pending:
        metrics.last_success = time.time()


def main():
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    client = Client(int(os.environ["TIMEWEB_CLUSTER_ID"]), os.environ["POD_NAMESPACE"],
                    "/var/run/timeweb/token")
    metrics = Metrics()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/metrics", "/healthz"):
                self.send_error(404)
                return
            body = (f"timeweb_control_plane_logs_last_success_timestamp_seconds {metrics.last_success}\n"
                    f"timeweb_control_plane_logs_errors_total {metrics.errors}\n"
                    f"timeweb_control_plane_logs_exported_records_total {metrics.records}\n"
                    f"timeweb_control_plane_logs_pending_scans {metrics.pending_scans}\n"
                    "# TYPE timeweb_control_plane_logs_diagnostic_stream_resets_total counter\n"
                    f"timeweb_control_plane_logs_diagnostic_stream_resets_total {metrics.diagnostic_stream_resets}\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("0.0.0.0", 9090), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    interval = max(30, int(os.environ.get("POLL_INTERVAL_SECONDS", "60")))
    while not stop.is_set():
        try:
            collect_cycle(client, metrics)
        except Exception as error:
            report_failure(error, metrics)
        stop.wait(interval)
    server.shutdown()


if __name__ == "__main__":
    main()
