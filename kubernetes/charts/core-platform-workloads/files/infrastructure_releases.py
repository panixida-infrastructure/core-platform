"""Read deployed versions and official release metadata; never perform upgrades."""

import copy
import http.server
import json
import os
from pathlib import Path
import re
import signal
import ssl
import threading
import time
import urllib.parse
import urllib.request


DAY = 86400
EVENT_WINDOW = 3600
DEFAULT_PATTERN = r"v?(\d+\.\d+\.\d+)"
KUBE_AUTH = Path("/var/run/secrets/kubernetes.io/serviceaccount")


def version(value):
    match = re.fullmatch(r"(\d+(?:\.\d+){0,3})(?:\+k0s\.(\d+))?", value)
    if not match:
        raise ValueError("Not a stable numeric version")
    parts = tuple(map(int, match[1].split(".")))
    return parts + (0,) * (4 - len(parts)) + (int(match[2] or 0),)


def extract(pattern, value):
    match = re.fullmatch(pattern, value)
    return match[1].replace("_", ".") if match else None


def image_parts(image):
    image = image.split("@", 1)[0]
    if ":" not in image.rsplit("/", 1)[-1]:
        return image, "latest"
    return image.rsplit(":", 1)


def installed_images(pod):
    specs = pod["spec"].get("containers", []) + pod["spec"].get("initContainers", [])
    status = pod.get("status", {})
    observed = {item["name"]: item for key in ("containerStatuses", "initContainerStatuses")
                for item in status.get(key, [])}
    for container in specs:
        if observed:
            item = observed.get(container.get("name"), {})
            if not item.get("imageID"):
                continue  # A desired image that cannot be pulled is not installed.
            image = item.get("image", container["image"])
            # containerd reports only a config digest for digest-pinned images.
            # The Pod reference retains the version tag beside the manifest digest.
            if re.fullmatch(r"sha256:[0-9a-f]{64}", image) or ":" not in image.rsplit("/", 1)[-1]:
                image = container["image"]
            yield image
        else:
            # A Pod with no container status yet has no observed installation.
            continue


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected redirect")


class Client:
    def __init__(self, timeweb_token="/var/run/timeweb/token"):
        self.timeweb_token = Path(timeweb_token)
        self.cache = {}

    def get(self, url, auth=None):
        if url in self.cache:
            return self.cache[url]
        headers = {"User-Agent": "core-platform-release-monitor", "Accept": "application/json"}
        context = ssl.create_default_context()
        if auth == "timeweb":
            if urllib.parse.urlsplit(url).netloc != "api.timeweb.cloud":
                raise ValueError("Invalid credential destination")
            headers["Authorization"] = "Bearer " + self.timeweb_token.read_text().strip()
        elif auth == "kubernetes":
            if urllib.parse.urlsplit(url).netloc != "kubernetes.default.svc":
                raise ValueError("Invalid credential destination")
            headers["Authorization"] = "Bearer " + (KUBE_AUTH / "token").read_text().strip()
            context = ssl.create_default_context(cafile=str(KUBE_AUTH / "ca.crt"))
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
        with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
            # Bound memory even when a public endpoint returns unexpected content.
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("Response too large")
            result = json.loads(body)
        self.cache[url] = result
        return result

    def kube_items(self, path):
        items, continuation = [], ""
        for _ in range(100):
            query = urllib.parse.urlencode({"limit": 200, "continue": continuation})
            data = self.get("https://kubernetes.default.svc" + path + "?" + query, "kubernetes")
            items.extend(data["items"])
            continuation = data["metadata"].get("continue", "")
            if not continuation:
                return items
        raise ValueError("Kubernetes pagination limit exceeded")

    def inventory(self):
        pods = self.kube_items("/api/v1/pods")
        # Some Helm charts expose their version only on Deployment metadata.
        # Do not use the desired template images as evidence of running versions.
        charts = [{"metadata": item["metadata"], "spec": {}}
                  for item in self.kube_items("/apis/apps/v1/deployments")]
        return pods + charts

    def timeweb(self, path):
        return self.get("https://api.timeweb.cloud/api/v1/" + path, "timeweb")

    def latest(self, component):
        source = component["source"]
        kind = source["kind"]
        pattern = source.get("pattern", DEFAULT_PATTERN)
        if kind == "github":
            repo = source["repo"]
            # Release assets make large pages enormous for multi-platform projects.
            # Include GitHub's designated latest release as well as recent branch releases.
            data = self.get(f"https://api.github.com/repos/{repo}/releases?per_page=10")
            designated = self.get(f"https://api.github.com/repos/{repo}/releases/latest")
            data = data + [designated]
            candidates = [(extract(pattern, item["tag_name"]),
                           f"https://github.com/{repo}/releases/tag/" + urllib.parse.quote(item["tag_name"], safe=""))
                          for item in data if not item["draft"] and not item["prerelease"]]
        elif kind == "dockerhub":
            repo = source["repo"]
            query = urllib.parse.urlencode({"page_size": 100, "name": source.get("filter", "")})
            base = f"https://hub.docker.com/v2/namespaces/{repo.split('/')[0]}/repositories/{repo.split('/')[1]}/tags"
            data = self.get(base + "?" + query)["results"]
            excluded = {item.get("digest") for item in data
                        if item["name"] in source.get("excludeAliases", [])}
            excluded.discard(None)
            candidates = [(extract(pattern, item["name"]), f"https://hub.docker.com/r/{repo}/tags?name=" + item["name"])
                          for item in data if item.get("digest") not in excluded]
        elif kind == "timeweb-kubernetes":
            candidates = [(extract(pattern, tag), "https://timeweb.cloud/docs/k8s/manage-cluster/version-update")
                          for tag in self.timeweb("k8s/k8s-versions")["k8s_versions"]]
        elif kind == "timeweb-postgresql":
            candidates = [(item["version"], "https://timeweb.cloud/docs/dbaas")
                          for item in self.timeweb("database-types")["types"]
                          if item["name"] == "postgresql" and not item["is_deprecated"]]
        else:
            raise ValueError("Unknown release source")
        candidates = [(v, url) for v, url in candidates if v]
        if not candidates:
            raise ValueError("No stable release matched the configured channel")
        latest, url = max(candidates, key=lambda item: version(item[0]))
        return {"version": latest, "url": url}


def deployed(component, pods):
    selector = component["installed"]
    found, invalid = set(), False
    for pod in pods:
        if pod["metadata"].get("deletionTimestamp") or pod.get("status", {}).get("phase") in ("Succeeded", "Failed"):
            continue
        if selector.get("namespace") and pod["metadata"]["namespace"] != selector["namespace"]:
            continue
        if "chartPattern" in selector:
            tag = pod["metadata"].get("labels", {}).get("helm.sh/chart", "")
            parsed = extract(selector["chartPattern"], tag)
            if parsed:
                found.add(parsed)
            continue
        for image in installed_images(pod):
            repo, tag = image_parts(image)
            if re.fullmatch(selector["imagePattern"], repo):
                parsed = extract(selector.get("tagPattern", DEFAULT_PATTERN), tag)
                if parsed:
                    found.add(parsed)
                else:
                    invalid = True
    if invalid:
        raise ValueError("Unrecognized deployed image tag")
    return sorted(found, key=lambda value: (version(value), value))


def inventory_only(pods, components, config):
    """Show provider bundles and unmapped infrastructure images instead of hiding them."""
    rows = {}
    patterns = [c["installed"]["imagePattern"] for c in components if "imagePattern" in c.get("installed", {})]
    for pod in pods:
        ns = pod["metadata"]["namespace"]
        if ns not in config["infrastructureNamespaces"] or pod.get("status", {}).get("phase") in ("Succeeded", "Failed"):
            continue
        if pod["metadata"].get("deletionTimestamp"):
            continue
        for image in installed_images(pod):
            repo, tag = image_parts(image)
            if any(re.fullmatch(pattern, repo) for pattern in patterns):
                continue
            managed = ns in config["providerNamespaces"]
            own = any(repo.startswith(prefix) for prefix in config["ownImagePrefixes"])
            status = "provider_managed" if managed else "own_build" if own else "untracked"
            rows.setdefault((repo, status), set()).add(tag)
    return [{"component": repo, "current_version": ", ".join(sorted(tags)), "latest_version": "—",
             "status": status, "release_url": "", "note": {
                 "provider_managed": "Updated with the Timeweb cluster; do not upgrade bundled images independently.",
                 "own_build": "Project delivery pipeline; no third-party upstream release.",
                 "untracked": "Add an official release source to the monitor catalog."}[status],
             "last_success": 0}
            for (repo, status), tags in sorted(rows.items())]


class Monitor:
    def __init__(self, config, state_file):
        self.config = config
        self.path = Path(state_file)
        # Corrupt state must fail visibly; silently resetting would resend every release.
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"schema": 1, "components": {}}
        if self.state.get("schema") != 1 or not isinstance(self.state.get("components"), dict):
            raise ValueError("Invalid monitor state")
        self.lock = threading.Lock()
        self.rows = []
        self.events = []
        self.inventory_success = 0
        self.inventory_last_success = 0
        self.state_success = 1

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def poll(self, client, now=None):
        now = time.time() if now is None else now
        try:
            pods = client.inventory()
        except Exception as error:
            log_failure("inventory", error)
            with self.lock:
                self.inventory_success = 0
                self.events = []
            return
        state = copy.deepcopy(self.state)
        rows, events = [], []
        for component in self.config["components"]:
            name = component["name"]
            record = state["components"].setdefault(name, {})
            row = {"component": name, "current_version": "unknown", "latest_version": record.get("latest", {}).get("version", "unknown"),
                   "release_url": record.get("latest", {}).get("url", ""), "note": component.get("note", ""),
                   "last_success": record.get("last_success", 0), "status": "inventory_failed"}
            try:
                selector = component["installed"]
                if selector.get("kind") == "timeweb-kubernetes":
                    data = client.timeweb(f"k8s/clusters/{self.config['kubernetesClusterId']}")["cluster"]
                    current = [extract(r"v(\d+\.\d+\.\d+\+k0s\.\d+)", data["k8s_version"])]
                elif selector.get("kind") == "timeweb-postgresql":
                    data = client.timeweb(f"dbs/{self.config['databaseClusterId']}")["db"]
                    current = [item["version"] for item in client.timeweb("database-types")["types"] if item["type"] == data["type"]]
                    if not current:
                        raise ValueError("Unknown database type")
                else:
                    current = deployed(component, pods)
                if None in current:
                    raise ValueError("Unknown provider version")
                row["current_version"] = ", ".join(current) if current else "not installed"
                if not current:
                    row["status"] = "not_installed"
                    rows.append(row)
                    continue
            except Exception as error:
                log_failure(name, error)
                rows.append(row)
                continue
            # Daily public release checks; retry failures hourly, independently per component.
            due = record.get("last_attempt", 0) + (DAY if record.get("success") else 3600) <= now
            if due:
                record["last_attempt"] = now
                try:
                    record["latest"] = client.latest(component)
                    record["success"] = True
                    record["last_success"] = now
                except Exception as error:
                    record["success"] = False
                    log_failure(name, error)
            latest = record.get("latest", {})
            row.update(latest_version=latest.get("version", "unknown"), release_url=latest.get("url", ""),
                       last_success=record.get("last_success", 0))
            if not record.get("success") or now - record.get("last_success", 0) > DAY + 3600:
                row["status"] = "check_failed"
            elif version(latest["version"]) > min(map(version, current)):
                row["status"] = "update_available"
                # High-water mark survives pod restarts, daily checks, rollbacks and mixed rollouts.
                if version(latest["version"]) > version(record.get("announced_version", "0")):
                    record["announced_version"] = latest["version"]
                    record["event_until"] = now + EVENT_WINDOW
                    record["event_current"] = row["current_version"]
                if now < record.get("event_until", 0):
                    events.append(row | {"current_version": record["event_current"]})
            else:
                row["status"] = "current"
            rows.append(row)
        rows.extend(inventory_only(pods, self.config["components"], self.config))
        try:
            self.save(state)
        except Exception as error:
            log_failure("state", error)
            with self.lock:
                self.state_success = 0
                self.events = []
            return
        with self.lock:
            self.state = state
            self.rows, self.events = rows, events
            self.inventory_success = self.state_success = 1
            self.inventory_last_success = now

    def render(self):
        lines = []

        def sample(name, value, labels=None):
            suffix = ""
            if labels:
                suffix = "{" + ",".join(f'{key}="{escape(value)}"' for key, value in labels.items()) + "}"
            lines.append(f"infrastructure_release_{name}{suffix} {value}")

        with self.lock:
            sample("inventory_success", self.inventory_success)
            sample("inventory_last_success_timestamp_seconds", self.inventory_last_success)
            sample("state_success", self.state_success)
            sample("catalog_components", len(self.config["components"]))
            for row in self.rows:
                labels = {key: value for key, value in row.items() if key != "last_success"}
                if not self.inventory_success:
                    labels["status"] = "inventory_failed"
                sample("info", 1, labels)
                sample("last_success_timestamp_seconds", row["last_success"], {"component": row["component"]})
            for event in self.events:
                sample("new_version", 1, {key: value for key, value in event.items() if key not in ("last_success", "status")})
        return "\n".join(lines) + "\n"


def escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def log_failure(component, error):
    # Do not log provider bodies, credential contents, headers or raw exception messages.
    print(json.dumps({"level": "warning", "message": "Release monitor check failed", "component": component,
                      "error_type": type(error).__name__, "http_status": getattr(error, "code", None)}), flush=True)


def main():
    config = json.loads(Path("/app/catalog.json").read_text())
    monitor = Monitor(config, "/state/releases.json")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/healthz", "/metrics"):
                self.send_error(404)
                return
            body = (monitor.render() if self.path == "/metrics" else "ok\n").encode()
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
        monitor.poll(Client())
        stop.wait(300)
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
