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
CORE_SMOKE_SCRIPT = r'''
set -eu
cli() { valkey-cli -e --raw -h "$VALKEY_HOST" -p "$VALKEY_PORT" --user "$VALKEY_USERNAME" "$@"; }
key="__core_platform_smoke:${HOSTNAME}"
hash_key="${key}:hash"
channel="${key}:pubsub"
subscriber=""
cleanup() {
  if [ -n "$subscriber" ]; then kill "$subscriber" 2>/dev/null || true; fi
  cli DEL "$key" "$hash_key" >/dev/null 2>&1 || true
}
trap cleanup EXIT
test "$(cli PING)" = PONG
test "$(cli ECHO connection-check)" = connection-check
test "$(cli ACL WHOAMI)" = "$VALKEY_USERNAME"
test "$(cli SET "$key" 41 EX 60 NX)" = OK
test "$(cli GET "$key")" = 41
test "$(cli EVAL "return redis.call('INCR', KEYS[1])" 1 "$key")" = 42
test "$(cli TTL "$key")" -gt 0
test "$(cli HSET "$hash_key" data cache-value)" = 1
test "$(cli EXPIRE "$hash_key" 60)" = 1
test "$(cli HGET "$hash_key" data)" = cache-value
printf 'MULTI\nINCR %s\nEXPIRE %s 60\nEXEC\n' "$key" "$key" | cli >/tmp/transaction.log
test "$(cat /tmp/transaction.log)" = "$(printf 'OK\nQUEUED\nQUEUED\n43\n1')"
test "$(cli GET "$key")" = 43
test "$(cli TTL "$key")" -gt 0
echo 'PASS: authenticated PING/ECHO, identity, strings, hashes, TTL, Lua, transaction'
'''

SMOKE_SCRIPT = CORE_SMOKE_SCRIPT + r'''
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
denied() {
  if reply="$(cli "$@" 2>&1)"; then
    echo "FAIL: unexpectedly permitted $1"
    exit 1
  fi
  printf '%s\n' "$reply" | grep -Eq '^(NOPERM |ERR unknown command)'
  echo "DENIED: $1"
}
denied CONFIG GET maxmemory
denied ACL USERS
echo 'PASS: authenticated PING/ECHO, identity, strings, hashes, TTL, Lua, transaction, Pub/Sub, CONFIG/ACL denials'
'''

DEFAULT_DISABLED_SCRIPT = r'''
set -eu
cli() { valkey-cli -e --raw -h "$VALKEY_HOST" -p "$VALKEY_PORT" --user "$VALKEY_USERNAME" "$@"; }
test "$(cli PING)" = PONG
test "$(cli ACL WHOAMI)" = "$VALKEY_USERNAME"
result="$(VALKEYCLI_AUTH="$VALKEY_DEFAULT_PASSWORD" cli --user default PING 2>&1 || true)"
if printf '%s\n' "$result" | grep -qx PONG; then
  echo 'FAIL: default bootstrap credentials still work'; exit 1
fi
printf '%s\n' "$result" | grep -Eq '(WRONGPASS|NOAUTH)'
result="$(env -u VALKEYCLI_AUTH valkey-cli -e --raw -h "$VALKEY_HOST" -p "$VALKEY_PORT" PING 2>&1 || true)"
printf '%s\n' "$result" | grep -q '^NOAUTH '
echo 'PASS: application authentication works; default bootstrap and unauthenticated access rejected'
'''

GATEWAY_SMOKE_SCRIPT = r'''
set -eu
cli() { valkey-cli -e --raw -h "$VALKEY_HOST" -p "$VALKEY_PORT" --user "$VALKEY_USERNAME" "$@"; }
key="__core_platform_smoke:gateway:${HOSTNAME}"
cleanup() { cli DEL "$key" >/dev/null 2>&1 || true; }
trap cleanup EXIT
test "$(cli PING)" = PONG
test "$(cli -3 PING)" = PONG
test "$(cli ACL WHOAMI)" = "$VALKEY_USERNAME"
printf 'INCRBY %s 3\nEXPIRE %s 60\nGET %s\n' "$key" "$key" "$key" | cli >/tmp/ratelimit.log
test "$(cat /tmp/ratelimit.log)" = "$(printf '3\n1\n3')"
test "$(cli TTL "$key")" -gt 0
refund="local current = redis.call('GET', KEYS[1])
if current == false then return 0 end
local value = math.floor(math.max(0, tonumber(current) - tonumber(ARGV[1])))
redis.call('SET', KEYS[1], tostring(value))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return value"
test "$(cli EVAL "$refund" 1 "$key" 2 60)" = 1
test "$(cli GET "$key")" = 1
test "$(cli TTL "$key")" -gt 0
test "$(cli EVAL "$refund" 1 "$key" 10 60)" = 0
test "$(cli DEL "$key")" = 1
test "$(cli EVAL "$refund" 1 "$key" 1 60)" = 0
test "$(cli EXISTS "$key")" = 0
echo 'PASS: gateway identity, RESP2/RESP3, INCRBY/EXPIRE/GET, TTL and Lua refunds'
denied() {
  if reply="$(cli "$@" 2>&1)"; then
    echo "FAIL: unexpectedly permitted $1"; exit 1
  fi
  printf '%s\n' "$reply" | grep -Eq '^(NOPERM |ERR unknown command)'
  echo "DENIED: $1"
}
denied PUBLISH "${key}:pubsub" gateway-probe
denied MULTI
denied CONFIG GET maxmemory
denied ACL USERS
'''


def request_json(url, headers=None, payload=None, method=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})}, method=method)
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


def openbao_headers():
    audience = "https://github.com/panixida-infrastructure/core-platform"
    oidc_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    jwt = request_json(
        oidc_url + "&audience=" + urllib.parse.quote(audience, safe=""),
        {"Authorization": "bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})["value"]
    bao_url = os.environ.get("OPENBAO_ADDR", "https://secrets.panixida.ru").rstrip("/")
    token = request_json(bao_url + "/v1/auth/jwt/login", payload={
        "role": "core-platform-github-actions", "jwt": jwt})["auth"]["client_token"]
    print(f"::add-mask::{token}", flush=True)
    return bao_url, {"X-Vault-Token": token}


def update_secret(path, headers, desired, remove=()):
    # Preserve unrelated service credentials and avoid overwriting concurrent updates.
    for attempt in range(5):
        try:
            current = request_json(path, headers)["data"]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            current = {"data": {}, "metadata": {"version": 0}}
        existing = current["data"]
        if all(existing.get(key) == value for key, value in desired.items()) and not any(
                key in existing for key in remove):
            return
        updated = {key: value for key, value in existing.items() if key not in remove}
        updated.update(desired)
        try:
            request_json(path, headers, {"options": {"cas": current["metadata"]["version"]},
                                         "data": updated})
            saved = request_json(path, headers)["data"]["data"]
            if any(saved.get(key) != value for key, value in desired.items()) or any(
                    key in saved for key in remove):
                raise RuntimeError("OpenBao credential verification failed")
            return
        except urllib.error.HTTPError as error:
            if error.code != 400 or attempt == 4:
                raise
            time.sleep(1)


def publish_credentials(config, passwords, bao_url, headers):
    for environment, username in config["users"].items():
        path = f"{bao_url}/v1/secret/data/applications/tactical-heroes-api/{environment}"
        desired = {"VALKEY_HOST": config["host"], "VALKEY_PORT": str(config["port"]),
                   "VALKEY_USERNAME": username, "VALKEY_PASSWORD": passwords[environment]}
        update_secret(path, headers, desired)
        print(f"Verified OpenBao applications/tactical-heroes-api/{environment}: {username}")


def publish_gateway_credentials(config, password, bao_url, headers):
    desired = {"VALKEY_HOST": config["host"], "VALKEY_PORT": str(config["port"]),
               "VALKEY_USERNAME": config["gateway"]["username"], "VALKEY_PASSWORD": password}
    update_secret(f"{bao_url}/v1/secret/data/core-platform/envoy-gateway", headers, desired)
    print(f"Verified OpenBao core-platform/envoy-gateway: {config['gateway']['username']}")


def publish_exporter_credentials(config, provider_headers, bao_url, bao_headers):
    exporters = request_json(
        f"https://api.timeweb.cloud/api/v1/dbs/{config['id']}/exporters",
        provider_headers)["exporters"]
    # This deployment is single-node; never silently select a different replica.
    if len(exporters) != 1:
        raise ValueError("Expected one managed Valkey exporter")
    exporter = exporters[0]
    if any(not isinstance(exporter.get(key), str) or not exporter[key].strip()
           for key in ("id", "login", "password")):
        raise ValueError("Incomplete managed Valkey exporter credentials")
    uuid.UUID(exporter["id"])
    update_secret(f"{bao_url}/v1/secret/data/core-platform/observability", bao_headers, {
        "OBSERVABILITY_TIMEWEB_VALKEY_EXPORTER_ID": exporter["id"],
        "OBSERVABILITY_TIMEWEB_VALKEY_EXPORTER_USERNAME": exporter["login"],
        "OBSERVABILITY_TIMEWEB_VALKEY_EXPORTER_PASSWORD": exporter["password"],
    })
    print("Published managed Valkey exporter credentials to OpenBao", flush=True)


def verify_users(users, config):
    by_login = {user["login"]: user for user in users}
    expected = {username: config["privileges"] for username in config["users"].values()}
    expected[config["gateway"]["username"]] = config["gateway"]["privileges"]
    for username, privileges in expected.items():
        user = by_login.get(username)
        if user is None or user.get("status") != "created":
            raise RuntimeError(f"Valkey user is not ready: {username}")
        instances = user.get("instances", [])
        if not instances or any(set(instance["privileges"]) != set(privileges)
                                for instance in instances):
            raise RuntimeError(f"Unexpected provider privileges: {username}")


def retire_default_user(base, headers, config):
    if config.get("retire_default_user") is not True:
        raise ValueError("Default Valkey user retirement must be enabled")
    path = f"{base}/databases/{config['id']}/admins"
    users = request_json(path, headers)["admins"]
    verify_users(users, config)
    default = next((user for user in users if user["login"] == "default"), None)
    if default is not None:
        request_json(f"{path}/{default['id']}", headers, method="DELETE")
    deadline = time.monotonic() + 90
    while True:
        remaining = request_json(path, headers)["admins"]
        if not any(user["login"] == "default" for user in remaining):
            verify_users(remaining, config)
            print("Default user removed from Timeweb")
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Default Valkey user was not retired by Timeweb")
        time.sleep(3)


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


def probe_workers(config, password, *, script=SMOKE_SCRIPT, default_password=""):
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
            "stringData": {"password": password, "default_password": default_password}})
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
                             "command": ["sh", "-c", script],
                             "securityContext": {"allowPrivilegeEscalation": False,
                                                 "capabilities": {"drop": ["ALL"]}},
                             "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                                           "limits": {"cpu": "100m", "memory": "64Mi"}},
                             "env": [
                                 {"name": "VALKEY_HOST", "value": config["host"]},
                                 {"name": "VALKEY_PORT", "value": str(config["port"])},
                                 {"name": "VALKEY_USERNAME", "value": config["username"]},
                                 {"name": "VALKEY_DEFAULT_PASSWORD", "valueFrom": {"secretKeyRef": {
                                     "name": name, "key": "default_password"}}},
                                 {"name": "VALKEYCLI_AUTH", "valueFrom": {"secretKeyRef": {
                                     "name": name, "key": "password"}}}]}]}})
            status = wait_for_probe(namespace, pod_name)
            phase = status.get("phase", "Unknown")
            logs = kubectl("-n", namespace, "logs", pod_name, check=False).stdout
            logs = logs.replace(password, "[REDACTED]")
            if default_password:
                logs = logs.replace(default_password, "[REDACTED]")
            print(f"User {config['username']}, worker {worker}: {phase}\n{logs}", flush=True)
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
    passwords = json.loads(os.environ["VALKEY_USER_PASSWORDS"])
    default_password = os.environ["VALKEY_DEFAULT_PASSWORD"]
    gateway_password = os.environ["VALKEY_GATEWAY_PASSWORD"]
    expected_users = {"development": "tactical_heroes_user_dev", "production": "tactical_heroes_user_prod"}
    if config["users"] != expected_users or set(passwords) != set(expected_users):
        raise ValueError("Unexpected Valkey application identities")
    if config["gateway"]["username"] != "envoy_gateway_user":
        raise ValueError("Unexpected Valkey gateway identity")
    all_passwords = [default_password, gateway_password, *passwords.values()]
    if any(len(password) != 30 for password in all_passwords):
        raise ValueError("Unexpected Valkey password length")
    if len(set(all_passwords)) != len(all_passwords):
        raise ValueError("Valkey users must have independent passwords")
    for password in all_passwords:
        print(f"::add-mask::{password}", flush=True)
    headers = {"Authorization": "Bearer " + os.environ["TIMEWEB_TOKEN"]}
    base = "https://api.timeweb.cloud/api/v1"
    db = request_json(f"{base}/databases/{config['id']}", headers)["db"]
    backups = request_json(f"{base}/dbs/{config['id']}/auto-backups", headers)["auto_backups_settings"]
    verify_database(db, backups, config)
    print(f"Verified Valkey {config['id']}: 9.1, preset 5067, private-only, no backups, noeviction")
    verify_users(request_json(f"{base}/databases/{config['id']}/admins", headers)["admins"], config)
    bao_url, bao_headers = openbao_headers()
    publish_exporter_credentials(config, headers, bao_url, bao_headers)
    publish_credentials(config, passwords, bao_url, bao_headers)
    publish_gateway_credentials(config, gateway_password, bao_url, bao_headers)
    probe_workers({**config, "username": config["gateway"]["username"]}, gateway_password,
                  script=GATEWAY_SMOKE_SCRIPT)
    for environment, username in config["users"].items():
        probe_workers({**config, "username": username}, passwords[environment], script=CORE_SMOKE_SCRIPT)
    retire_default_user(base, headers, config)
    for environment, username in config["users"].items():
        probe_workers({**config, "username": username}, passwords[environment],
                      script=DEFAULT_DISABLED_SCRIPT, default_password=default_password)
    update_secret(f"{bao_url}/v1/secret/data/core-platform/applications", bao_headers, {},
                  remove=("VALKEY_HOST", "VALKEY_PORT", "VALKEY_USERNAME", "VALKEY_PASSWORD"))
    print("Retired bootstrap credentials removed from the current shared OpenBao secret")
    # Verify Pub/Sub and command restrictions after bootstrap retirement.
    for environment, username in config["users"].items():
        probe_workers({**config, "username": username}, passwords[environment])


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
