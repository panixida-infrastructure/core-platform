"""Forward managed control-plane logs to the existing OpenTelemetry collector."""

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
import urllib.parse
import urllib.request


FIELDS = re.compile(r'(\w+)=("(?:\\.|[^"\\])*"|[^\s]+)')
LEVELS = {"trace": (1, "Trace"), "debug": (5, "Debug"), "info": (9, "Information"),
          "warn": (13, "Warning"), "warning": (13, "Warning"),
          "error": (17, "Error"), "fatal": (21, "Critical"), "panic": (21, "Critical")}
SCOPES = ("k0scontroller.service", "cloud-provider-timeweb-cloud.service",
          "tw-kube-healer.service", "twcp.service")
OVERLAP_SECONDS = 120


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
        match = re.match(r'^[IWEF](\d{2})(\d{2}) (\d{2}:\d{2}:\d{2})(?: GMT (\d{6})|\.(\d{6}))', message)
        if match:
            reference = dt.datetime.fromtimestamp(observed, dt.timezone.utc)
            candidates = []
            for year in (reference.year - 1, reference.year, reference.year + 1):
                try:
                    value = dt.datetime.fromisoformat(
                        f"{year}-{match[1]}-{match[2]}T{match[3]}.{match[4] or match[5]}+00:00")
                    candidates.append(value.timestamp())
                except ValueError:
                    pass
            if candidates:
                timestamp = min(candidates, key=lambda value: abs(value - observed))
    severity, text = LEVELS.get(level, LEVELS["info"])
    attrs = [attribute("alert_owner", "core-platform"), attribute("log.source", "timeweb-api"),
             attribute("timeweb.log.scope", scope)]
    if timestamp is None:
        attrs.append(attribute("timeweb.timestamp.missing", "true"))
    record = {"observedTimeUnixNano": str(int(observed * 1_000_000_000)),
              "severityNumber": severity, "severityText": text,
              "body": {"stringValue": message}, "attributes": attrs}
    if timestamp is not None:
        record["timeUnixNano"] = str(int(timestamp * 1_000_000_000))
    component = fields.get("component", scope.removesuffix(".service"))
    return component, timestamp, record


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected HTTP redirect")


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
    def request(opener, url, headers=None, body=None, method=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with opener.open(req, timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RuntimeError("Response exceeds size limit")
            return json.loads(raw) if raw else {}

    def timeweb(self, path, query=None):
        with open(self.token_file, encoding="utf-8") as source:
            token = source.read().strip()
        url = "https://api.timeweb.cloud/api/v1/k8s/clusters/" + str(self.cluster) + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return self.request(self.public, url, {"Authorization": "Bearer " + token})

    def kube_headers(self):
        with open("/var/run/secrets/kubernetes.io/serviceaccount/token", encoding="utf-8") as source:
            token = source.read().strip()
        return {"Authorization": "Bearer " + token, "Content-Type": "application/merge-patch+json"}

    def load_state(self):
        response = self.request(self.kube, self.state_url, self.kube_headers())
        return json.loads(response.get("data", {}).get("state.json", "{}"))

    def save_state(self, state):
        self.request(self.kube, self.state_url, self.kube_headers(),
                     {"data": {"state.json": json.dumps(state)}}, "PATCH")

    def export(self, node, records):
        groups = {}
        for component, _, record in records:
            groups.setdefault(component, []).append(record)
        resources = []
        for component, logs in groups.items():
            attrs = {"service.name": component, "platform.name": "core-platform",
                     "deployment.environment": "production", "k8s.cluster.name": "core-platform",
                     "host.name": "master-" + node["node_ip"], "cloud.provider": "timeweb",
                     "timeweb.node.id": node["id"], "node.role": "control-plane"}
            resources.append({"resource": {"attributes": [attribute(k, v) for k, v in attrs.items()]},
                              "scopeLogs": [{"scope": {"name": "timeweb-control-plane-logs"},
                                             "logRecords": logs}]})
        if not resources:
            return
        response = self.request(self.public, os.environ["OTLP_LOGS_ENDPOINT"],
                                {"Content-Type": "application/json"}, {"resourceLogs": resources})
        partial = response.get("partialSuccess", {})
        if int(partial.get("rejectedLogRecords", 0)):
            raise RuntimeError("OpenTelemetry collector rejected log records")


def collect_node(client, node, state, now, scope="k0scontroller.service", page_limit=20):
    """Overlap polls to tolerate timestamp ties and delayed logs; export before checkpoint."""
    key = str(node["id"]) + ":" + scope
    previous = state.get(key, {})
    cutoff = previous.get("watermark", now - 180) - OVERLAP_SECONDS
    known = previous.get("seen", {})
    seen = {}
    watermark = previous.get("watermark", cutoff)
    cursor = None
    pending = []
    pending_bytes = 0
    complete = False
    for _ in range(page_limit):
        query = {"node_ids": node["id"], "limit": 500, "period": "10080m",
                 "scope": scope, "timezone": "UTC"}
        if cursor:
            query["cursor"] = cursor
        page = client.timeweb("/logs", query)
        lines = page.get("k8s_logs", [])
        if not lines:
            complete = True
            break
        page_is_older = True
        for line in lines:
            fingerprint = hashlib.sha256(line.encode()).hexdigest()
            record = normalize(line, node, now, scope)
            timestamp = record[1]
            if timestamp is None:
                timestamp = known.get(fingerprint, {}).get("time", now)
            if timestamp < cutoff:
                continue
            page_is_older = False
            watermark = max(watermark, timestamp)
            entry = seen.setdefault(fingerprint, {"time": timestamp, "count": 0})
            entry["count"] += 1
            if entry["count"] <= known.get(fingerprint, {}).get("count", 0):
                continue
            pending_bytes += len(line.encode())
            if pending_bytes > 8 * 1024 * 1024:
                raise RuntimeError("Log backlog exceeds memory bound; checkpoint was retained")
            pending.append(record)
        if page_is_older:
            complete = True
            break
        next_cursor = page.get("meta", {}).get("next_cursor")
        if not next_cursor:
            complete = True
            break
        if next_cursor == cursor:
            raise RuntimeError("Provider returned a non-advancing cursor")
        cursor = next_cursor
    if not complete:
        raise RuntimeError("Log backlog exceeds page bound; checkpoint was retained")
    if not seen:
        return 0
    records = list(reversed(pending))
    for offset in range(0, len(records), 500):
        client.export(node, records[offset:offset + 500])
    checkpoint = {"watermark": watermark, "seen": {
        key: value for key, value in seen.items() if value["time"] >= watermark - OVERLAP_SECONDS}}
    if checkpoint != previous:
        updated = dict(state, **{key: checkpoint})
        client.save_state(updated)
        state.update(updated)
    return len(records)


class Metrics:
    last_success = 0
    errors = 0
    records = 0


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
                    f"timeweb_control_plane_logs_exported_records_total {metrics.records}\n").encode()
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
            state = client.load_state()
            nodes = client.timeweb("/master-nodes").get("nodes", [])
            masters = [node for node in nodes if node.get("type") == "master"]
            if not masters:
                raise RuntimeError("Provider returned no master nodes")
            for node in masters:
                for scope in SCOPES:
                    metrics.records += collect_node(client, node, state, time.time(), scope)
            metrics.last_success = time.time()
        except Exception as error:
            metrics.errors += 1
            # Never print request headers, credential values, or provider response bodies.
            print(json.dumps({"level": "error", "message": "Control-plane log collection failed",
                              "error_type": type(error).__name__,
                              "http_status": getattr(error, "code", None)}), flush=True)
        stop.wait(interval)
    server.shutdown()


if __name__ == "__main__":
    main()
