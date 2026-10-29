#!/usr/bin/env python3
"""Verify the IaC-owned Valkey, save credentials, and probe each Kubernetes worker."""

import ipaddress
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


IMAGE = "valkey/valkey:9.1.3-alpine@sha256:bf09d58e96d6b4524a15396b65b242bbe18beebbcc15a04d8ee75ddac62fae0b"
SMOKE_SCRIPT = r'''
set -eu
cli() { valkey-cli -e --raw -h "$VALKEY_HOST" -p "$VALKEY_PORT" --user "$VALKEY_USERNAME" "$@"; }
key="__core_platform_smoke:${HOSTNAME}"
channel="${key}:pubsub"
subscriber=""
cleanup() {
  if [ -n "$subscriber" ]; then kill "$subscriber" 2>/dev/null || true; fi
  cli DEL "$key" >/dev/null 2>&1 || true
}
trap cleanup EXIT
test "$(cli PING)" = PONG
cli INFO memory | tr -d '\r' >/tmp/memory.info
grep -qx 'maxmemory_policy:noeviction' /tmp/memory.info
cli INFO server | grep -E '^valkey_version:9\.1\.'
test "$(cli SET "$key" 41 EX 60 NX)" = OK
test "$(cli GET "$key")" = 41
test "$(cli EVAL "return redis.call('INCR', KEYS[1])" 1 "$key")" = 42
test "$(cli TTL "$key")" -gt 0
timeout 20 valkey-cli -e --raw -h "$VALKEY_HOST" -p "$VALKEY_PORT" --user "$VALKEY_USERNAME" SUBSCRIBE "$channel" >/tmp/pubsub.log &
subscriber=$!
for attempt in $(seq 1 10); do
  if grep -qx subscribe /tmp/pubsub.log; then break; fi
  sleep 1
done
grep -qx subscribe /tmp/pubsub.log
test "$(cli PUBLISH "$channel" valkey-smoke-message)" -ge 1
for attempt in $(seq 1 10); do
  if grep -qx valkey-smoke-message /tmp/pubsub.log; then break; fi
  sleep 1
done
grep -qx valkey-smoke-message /tmp/pubsub.log
grep -E '^(used_memory_human|maxmemory_human|maxmemory_policy):' /tmp/memory.info
echo 'PASS: authenticated PING, noeviction, SET/GET, TTL, Lua, Pub/Sub'
'''


def request_json(url, headers=None, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response) if response.status != 204 else {}


def verify_database(db, backups, config):
    expected = {"type": "valkey9_1", "preset_id": 5067, "cpu": 1,
                "ram": 2048, "availability_zone": "msk-1",
                "status": "started", "is_enabled_public_network": False,
                "is_enabled_public_ipv6": False, "is_autobackups_enabled": False,
                "is_backup_schedule_enabled": False}
    for key, value in expected.items():
        if db.get(key) != value:
            raise ValueError(f"Unexpected Valkey setting: {key}")
    if db.get("disk", {}).get("size") != 10 * 1024 * 1024:
        raise ValueError("Unexpected Valkey disk size (API reports KiB)")
    if str(db["id"]) != str(config["id"]):
        raise ValueError("Unexpected Valkey cluster ID")
    if db["config_parameters"].get("maxmemory-policy") != "noeviction":
        raise ValueError("Valkey eviction policy is not noeviction")
    if backups.get("is_enabled") is not False:
        raise ValueError("Valkey automatic backups are not disabled")
    for network in db["networks"]:
        if network["type"] == "public" and network.get("ips"):
            raise ValueError("Valkey has a public IP")
    private_ips = [address["ip"] for network in db["networks"]
                   if network["type"] == "local" and network["id"] == config["network_id"]
                   for address in network["ips"]]
    if config["host"] not in private_ips or not ipaddress.ip_address(config["host"]).is_private:
        raise ValueError("Valkey is not on the expected private network")


def publish_credentials(config, password):
    audience = "https://github.com/panixida-infrastructure/core-platform"
    oidc_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    jwt = request_json(
        oidc_url + "&audience=" + urllib.parse.quote(audience, safe=""),
        {"Authorization": "bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})["value"]
    bao_url = os.environ.get("OPENBAO_ADDR", "https://secrets.panixida.ru").rstrip("/")
    token = request_json(bao_url + "/v1/auth/jwt/login", payload={
        "role": "core-platform-github-actions", "jwt": jwt})["auth"]["client_token"]
    print(f"::add-mask::{token}", flush=True)
    headers = {"X-Vault-Token": token}
    path = bao_url + "/v1/secret/data/core-platform/applications"
    desired = {"VALKEY_HOST": config["host"], "VALKEY_PORT": str(config["port"]),
               "VALKEY_USERNAME": config["username"], "VALKEY_PASSWORD": password}
    # Preserve unrelated service credentials and avoid overwriting concurrent updates.
    for attempt in range(5):
        current = request_json(path, headers)["data"]
        existing = current["data"]
        if all(existing.get(key) == value for key, value in desired.items()):
            return
        try:
            request_json(path, headers, {"options": {"cas": current["metadata"]["version"]},
                                         "data": {**existing, **desired}})
            return
        except urllib.error.HTTPError as error:
            if error.code != 400 or attempt == 4:
                raise
            time.sleep(1)


def kubectl(*args, manifest=None, check=True):
    result = subprocess.run(
        ["kubectl", "--request-timeout=30s", *args],
        input=None if manifest is None else json.dumps(manifest),
        text=True, capture_output=True, timeout=150)
    if check and result.returncode:
        # kubectl can echo a rejected manifest, including Secret data.
        raise RuntimeError(f"kubectl {args[0]} failed (exit {result.returncode})")
    return result


def wait_for_probe(namespace, pod_name):
    deadline = time.monotonic() + 110
    while True:
        pod = json.loads(kubectl("-n", namespace, "get", "pod", pod_name, "-o", "json").stdout)
        status = pod["status"]
        if status.get("phase") in ("Succeeded", "Failed") or time.monotonic() >= deadline:
            return status
        time.sleep(2)


def probe_workers(config, password):
    nodes = json.loads(kubectl("get", "nodes", "-o", "json").stdout)["items"]
    workers = [node["metadata"]["name"] for node in nodes
               if not node["spec"].get("unschedulable", False)
               and "node-role.kubernetes.io/control-plane" not in node["metadata"].get("labels", {})
               and any(c["type"] == "Ready" and c["status"] == "True"
                       for c in node["status"]["conditions"])]
    if not workers:
        raise RuntimeError("No ready Kubernetes workers for the Valkey check")
    namespace = "default"
    name = "valkey-smoke-" + uuid.uuid4().hex[:12]
    pods = []
    try:
        kubectl("create", "-f", "-", manifest={
            "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace},
            "stringData": {"password": password}})
        for index, worker in enumerate(workers):
            pod_name = f"{name}-{index}"
            pods.append(pod_name)
            kubectl("create", "-f", "-", manifest={
                "apiVersion": "v1", "kind": "Pod",
                "metadata": {"name": pod_name, "namespace": namespace},
                "spec": {"nodeName": worker, "restartPolicy": "Never",
                         "automountServiceAccountToken": False, "activeDeadlineSeconds": 90,
                         "securityContext": {"runAsNonRoot": True, "runAsUser": 1000,
                                             "seccompProfile": {"type": "RuntimeDefault"}},
                         "containers": [{"name": "check", "image": IMAGE,
                             "command": ["sh", "-c", SMOKE_SCRIPT],
                             "securityContext": {"allowPrivilegeEscalation": False,
                                                 "capabilities": {"drop": ["ALL"]}},
                             "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                                           "limits": {"cpu": "100m", "memory": "64Mi"}},
                             "env": [
                                 {"name": "VALKEY_HOST", "value": config["host"]},
                                 {"name": "VALKEY_PORT", "value": str(config["port"])},
                                 {"name": "VALKEY_USERNAME", "value": config["username"]},
                                 {"name": "VALKEYCLI_AUTH", "valueFrom": {"secretKeyRef": {
                                     "name": name, "key": "password"}}}]}]}})
            status = wait_for_probe(namespace, pod_name)
            phase = status.get("phase", "Unknown")
            logs = kubectl("-n", namespace, "logs", pod_name, check=False).stdout
            print(f"Worker {worker}: {phase}\n{logs.replace(password, '[REDACTED]')}", flush=True)
            if phase != "Succeeded":
                exits = [c.get("state", {}).get("terminated", {}).get("exitCode")
                         for c in status.get("containerStatuses", [])]
                raise RuntimeError(f"Valkey check failed on worker {worker}: {phase}, exit codes {exits}")
    finally:
        cleanup_failed = False
        for pod_name in pods:
            cleanup_failed |= kubectl("-n", namespace, "delete", "pod", pod_name,
                                      "--ignore-not-found", "--wait=false", check=False).returncode != 0
        cleanup_failed |= kubectl("-n", namespace, "delete", "secret", name,
                                  "--ignore-not-found", check=False).returncode != 0
        if cleanup_failed:
            raise RuntimeError(f"Could not remove all temporary resources with prefix {name}")


def main():
    config = json.loads(os.environ["VALKEY_CONFIG"])
    password = os.environ["VALKEY_PASSWORD"]
    if len(password) != 30:
        raise ValueError("Unexpected Valkey password length")
    headers = {"Authorization": "Bearer " + os.environ["TIMEWEB_TOKEN"]}
    base = "https://api.timeweb.cloud/api/v1"
    db = request_json(f"{base}/databases/{config['id']}", headers)["db"]
    backups = request_json(f"{base}/dbs/{config['id']}/auto-backups", headers)["auto_backups_settings"]
    verify_database(db, backups, config)
    print(f"Verified Valkey {config['id']}: 9.1, preset 5067, private-only, no backups, noeviction")
    publish_credentials(config, password)
    print("Valkey credentials are stored in OpenBao core-platform/applications")
    probe_workers(config, password)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        sys.exit(f"Managed Valkey verification failed: HTTP {error.code}")
    except (ValueError, RuntimeError) as error:
        sys.exit(f"Managed Valkey verification failed: {error}")
    except (KeyError, urllib.error.URLError, subprocess.TimeoutExpired) as error:
        # Avoid response bodies, request manifests, and credentials in CI logs.
        sys.exit(f"Managed Valkey verification failed: {type(error).__name__}")
