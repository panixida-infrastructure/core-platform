"""Expose managed PostgreSQL physical backup metadata from read-only Timeweb API calls."""

import datetime as dt
import http.server
import json
import os
from pathlib import Path
import signal
import threading
import time
import urllib.error
import urllib.request


BACKUP_TYPES = ("auto", "manual")
BACKUP_STATUSES = ("precreate", "delete", "shutdown", "recover", "create", "fail", "done")
PAGE_SIZE = 100
MAX_PAGES = 100
REQUEST_TIMEOUT_SECONDS = 20


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected HTTP redirect")


class Client:
    def __init__(self, cluster_id, token_file):
        if not str(cluster_id).isdecimal() or int(cluster_id) <= 0:
            raise ValueError("Cluster ID must be a positive integer")
        self.base_url = f"https://api.timeweb.cloud/api/v1/dbs/{int(cluster_id)}"
        self.token_file = Path(token_file)
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, path):
        # Re-read the mounted credential on every request to support secret rotation.
        request = urllib.request.Request(self.base_url + path, headers={
            "Authorization": "Bearer " + self.token_file.read_text().strip(),
            "Accept": "application/json"}, method="GET")
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 2:
                    raise
            time.sleep(attempt + 1)

    def collect(self):
        settings = self.get("/auto-backups")["auto_backups_settings"]
        backups, ids = [], set()
        expected_total = None
        for page in range(MAX_PAGES):
            response = self.get(f"/backups?limit={PAGE_SIZE}&offset={page * PAGE_SIZE}")
            total = response["meta"]["total"]
            items = response["backups"]
            if type(total) is not int or total < 0 or not isinstance(items, list):
                raise ValueError("Invalid backup pagination")
            if expected_total is not None and total != expected_total:
                raise ValueError("Backup inventory changed during pagination")
            expected_total = total
            for backup in items:
                backup_id = backup["id"]
                if type(backup_id) is not int or backup_id <= 0 or backup_id in ids:
                    raise ValueError("Invalid or duplicate backup ID")
                ids.add(backup_id)
            backups.extend(items)
            if len(backups) == total:
                return summarize(settings, backups, time.time())
            if len(backups) > total or len(items) != PAGE_SIZE:
                raise ValueError("Incomplete backup inventory")
        raise ValueError("Backup pagination limit exceeded")


def summarize(settings, backups, now):
    # Reject incomplete/unknown data instead of publishing a false 'no backups' result.
    enabled, copies = settings["is_enabled"], settings["copy_count"]
    if type(enabled) is not bool or type(copies) is not int or copies < 0:
        raise ValueError("Invalid automatic backup settings")
    counts = {(kind, status): 0 for kind in BACKUP_TYPES for status in BACKUP_STATUSES}
    last_success = dict.fromkeys(BACKUP_TYPES, 0)
    last_attempt = dict.fromkeys(BACKUP_TYPES, (0, False))
    for backup in backups:
        kind, status = backup["type"], backup["status"]
        if kind not in BACKUP_TYPES or status not in BACKUP_STATUSES:
            raise ValueError("Unknown backup type or status")
        created = dt.datetime.fromisoformat(backup["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None or not 0 < created.timestamp() <= now + 300:
            raise ValueError("Invalid backup creation time")
        timestamp = created.timestamp()
        counts[kind, status] += 1
        if status == "done":
            last_success[kind] = max(last_success[kind], timestamp)
        if timestamp > last_attempt[kind][0]:
            last_attempt[kind] = (timestamp, status == "fail")
    return {"enabled": int(enabled), "copies": copies, "counts": counts,
            "last_success": last_success, "last_attempt": last_attempt}


def escape_label(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Metrics:
    def __init__(self, cluster_id, cluster_name, max_age_seconds):
        self.labels = {"db_cluster_id": str(cluster_id), "db_cluster": cluster_name}
        self.max_age_seconds = max_age_seconds
        self.lock = threading.Lock()
        self.snapshot = None
        self.poll_success = 0
        self.last_poll_success = 0
        self.errors = 0

    def poll(self, client):
        try:
            snapshot = client.collect()
        except Exception as error:
            with self.lock:
                self.poll_success = 0
                self.errors += 1
            # Never include credentials, request headers or provider response bodies.
            # Alert through the stale-monitor rule; transient API errors are not log alerts.
            print(json.dumps({"level": "warning", "message": "Timeweb backup polling failed",
                              "error_type": type(error).__name__,
                              "http_status": getattr(error, "code", None)}), flush=True)
        else:
            with self.lock:
                self.snapshot = snapshot
                self.poll_success = 1
                self.last_poll_success = time.time()

    def render(self):
        lines = []

        def metric(suffix, help_text, samples, kind="gauge"):
            name = "timeweb_postgres_backup_" + suffix
            lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"])
            for value, extra in samples:
                labels = ",".join(f'{key}="{escape_label(value)}"'
                                  for key, value in (self.labels | extra).items())
                lines.append(f"{name}{{{labels}}} {value}")

        with self.lock:
            metric("poll_success", "Whether the most recent complete API poll succeeded.",
                   [(self.poll_success, {})])
            metric("poll_last_success_timestamp_seconds", "Time of the last complete successful API poll.",
                   [(self.last_poll_success, {})])
            metric("poll_errors_total", "Failed API polling cycles.", [(self.errors, {})], "counter")
            metric("max_age_seconds", "Maximum allowed age of a successful automatic backup.",
                   [(self.max_age_seconds, {})])
            if self.snapshot is not None:
                snapshot = self.snapshot
                metric("auto_enabled", "Whether Timeweb automatic backups are enabled.",
                       [(snapshot["enabled"], {})])
                metric("retention_copies", "Configured automatic backup retention count.",
                       [(snapshot["copies"], {})])
                metric("count", "Current provider backup inventory by type and status.",
                       [(count, {"backup_type": kind, "status": status})
                        for (kind, status), count in snapshot["counts"].items()])
                metric("last_success_timestamp_seconds",
                       "Creation time of the newest backup currently in done state; zero if absent.",
                       [(timestamp, {"backup_type": kind})
                        for kind, timestamp in snapshot["last_success"].items()])
                metric("last_attempt_failed", "Whether the newest backup of this type is in fail state.",
                       [(int(failed), {"backup_type": kind})
                        for kind, (_, failed) in snapshot["last_attempt"].items()])
        return "\n".join(lines) + "\n"


def main():
    cluster_id = os.environ["TIMEWEB_DATABASE_CLUSTER_ID"]
    client = Client(cluster_id, "/var/run/timeweb/token")
    metrics = Metrics(cluster_id, os.environ["TIMEWEB_DATABASE_CLUSTER_NAME"],
                      int(os.environ.get("BACKUP_MAX_AGE_SECONDS", "129600")))
    interval = max(60, int(os.environ.get("POLL_INTERVAL_SECONDS", "900")))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                body = b"ok\n"
            elif self.path == "/metrics":
                body = metrics.render().encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("0.0.0.0", 9090), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while not stop.is_set():
        metrics.poll(client)
        stop.wait(interval)
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
