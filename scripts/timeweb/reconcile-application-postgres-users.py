#!/usr/bin/env python3
"""Reconcile application database identities without dropping their tables."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


COMMON_PATH = "core-platform/applications"
REVISION_KEY = "APPLICATION_DB_PASSWORD_REVISION"
CONNECTION_KEY = "ConnectionStrings__PostgreSqlConnectionString"
PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "TRUNCATE",
              "REFERENCES", "TRIGGER", "TEMPORARY", "CONNECT"]


def request(url, headers=None, method="GET", body=None):
    headers = dict(headers or {})
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as error:
        # Provider errors can echo request bodies containing credentials.
        raise RuntimeError(f"{method} {urllib.parse.urlsplit(url).path}: HTTP {error.code}") from None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def select_user(item, admins):
    by_name = {admin["login"]: admin for admin in admins}
    if item["login"] in by_name:
        return by_name[item["login"]]
    aliases = [by_name[name] for name in item["aliases"] if name in by_name]
    if len(aliases) > 1:
        raise RuntimeError(f"Ambiguous existing users for {item['database']}")
    return aliases[0] if aliases else None


def connection_credentials(connection, login, password):
    # Only these two fields change; host, database, TLS and other options stay exact.
    for key, value in (("Username", login), ("Password", password)):
        pattern = rf"(^|;)(\s*{key}\s*=)[^;]*"
        if len(re.findall(pattern, connection, re.I)) != 1:
            raise RuntimeError(f"Expected one {key} in the connection string")
        if any(char in value for char in ';\r\n\"\''):
            raise RuntimeError("Credential requires unsupported connection-string escaping")
        connection = re.sub(pattern, lambda match: match[1] + match[2] + value,
                            connection, flags=re.I)
    return connection


def run(command, *, env=None, binary=False):
    result = subprocess.run(command, env=env, capture_output=True,
                            text=not binary, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed with exit code {result.returncode}")
    return result.stdout


class Reconciler:
    def __init__(self, config):
        self.config = config
        self.tw_headers = {"Authorization": "Bearer " + os.environ["TIMEWEB_TOKEN"]}
        self.bao_address = os.environ.get("OPENBAO_ADDR", "https://secrets.panixida.ru").rstrip("/")
        self.bao_headers = self.login()
        clusters = self.tw("/databases?limit=100")["dbs"]
        matches = [c for c in clusters if c["name"] == config["cluster_name"]
                   and c["availability_zone"] == config["availability_zone"]]
        if len(matches) != 1:
            raise RuntimeError("Expected exactly one managed PostgreSQL cluster")
        cluster = matches[0]
        self.cluster_id = cluster["id"]
        self.host = next(ip["ip"] for network in cluster["networks"]
                         if network["type"] == "public" for ip in network["ips"]
                         if ip["type"] == "ipv_4")
        self.base = f"/databases/{self.cluster_id}"
        self.instances = self.tw(self.base + "/instances?limit=100")["instances"]

    def login(self):
        token = os.environ.get("OPENBAO_TOKEN")
        if not token:
            audience = "https://github.com/panixida-infrastructure/core-platform"
            oidc = request(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=" +
                           urllib.parse.quote(audience, safe=""),
                           {"Authorization": "bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
            token = request(self.bao_address + "/v1/auth/jwt/login", method="POST",
                            body={"role": "core-platform-github-actions", "jwt": oidc["value"]})["auth"]["client_token"]
        self.login_time = time.monotonic()
        return {"X-Vault-Token": token}

    def tw(self, path, method="GET", body=None):
        return request("https://api.timeweb.cloud/api/v1" + path, self.tw_headers, method, body)

    def bao(self, path):
        if not os.environ.get("OPENBAO_TOKEN") and time.monotonic() - self.login_time > 600:
            self.bao_headers = self.login()
        return request(self.bao_address + "/v1/secret/data/" + path, self.bao_headers)["data"]

    def merge_secret(self, path, updates):
        before = self.bao(path)
        expected = before["data"] | updates
        if expected == before["data"]:
            return False
        request(self.bao_address + "/v1/secret/data/" + path, self.bao_headers, "POST",
                {"options": {"cas": before["metadata"]["version"]}, "data": expected})
        if self.bao(path)["data"] != expected:
            raise RuntimeError(f"OpenBao verification failed: {path}")
        print(f"OpenBao verified: {path}", flush=True)
        return True

    def admins(self):
        return self.tw(self.base + "/admins?limit=100")["admins"]

    def wait_user(self, admin_id, absent=False):
        for attempt in range(150):
            admin = next((a for a in self.admins() if a["id"] == admin_id), None)
            if absent and admin is None:
                return None
            if not absent and admin and admin["status"] == "created":
                return admin
            if attempt % 6 == 0:
                print(f"Waiting for Timeweb user {admin_id}: {admin['status'] if admin else 'absent'}", flush=True)
            time.sleep(4)
        raise RuntimeError(f"Timeweb user {admin_id} did not converge")

    def sql(self, admin, database, query):
        env = os.environ | {"PGPASSWORD": admin["password"], "PGSSLMODE": "require",
                            "PGCONNECT_TIMEOUT": "15"}
        return run(["psql", "-X", "-w", "-h", self.host, "-U", admin["login"],
                    "-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-c", query], env=env).strip()

    def objects(self, admin, database):
        return self.sql(admin, database, "SELECT coalesce(json_agg(row(c.oid,n.nspname,c.relname,c.relkind) ORDER BY c.oid),'[]') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_';")

    def plan(self):
        admins = self.admins()
        common = self.bao(COMMON_PATH)["data"]
        rotate = common.get(REVISION_KEY) != self.config["password_revision"]
        for item in self.config["users"]:
            if not any(db["name"] == item["database"] for db in self.instances):
                raise RuntimeError(f"Database is missing: {item['database']}")
            current = select_user(item, admins)
            connection_credentials(self.bao(item["secret_path"])["data"][CONNECTION_KEY], item["login"], "validation")
            print(json.dumps({"database": item["database"], "current_user": current["login"] if current else None,
                              "desired_user": item["login"], "rotate_password": rotate,
                              "preserve_owned_objects": True, "namespace": item["namespace"]}))
        print("Retire users: " + ", ".join(a["login"] for a in admins
                                          if a["login"] in self.config["retired_users"]))

    def reconcile_user(self, item, password):
        admin = select_user(item, self.admins())
        instance = next(db for db in self.instances if db["name"] == item["database"])
        before = self.objects(admin, item["database"]) if admin else None
        old_oid = self.sql(admin, item["database"], "SELECT oid FROM pg_roles WHERE rolname=current_user") if admin else None
        payload = {"login": item["login"], "password": password, "for_all": False,
                   "instance_id": instance["id"], "privileges": PRIVILEGES}
        if admin:
            permissions = next((x["privileges"] for x in admin["instances"]
                                if x["instance_id"] == instance["id"]), [])
            if admin["login"] != item["login"] or admin["password"] != password or set(permissions) != set(PRIVILEGES):
                self.tw(self.base + f"/admins/{admin['id']}", "PATCH", payload)
                admin = self.wait_user(admin["id"])
        else:
            created = self.tw(self.base + "/admins", "POST", payload | {"host": "%"})
            admin = self.wait_user(created["admin"]["id"])
        if admin["login"] != item["login"] or admin["password"] != password:
            raise RuntimeError(f"Database credential verification failed: {item['login']}")
        if before is not None and (self.objects(admin, item["database"]) != before or
                                  self.sql(admin, item["database"], "SELECT oid FROM pg_roles WHERE rolname=current_user") != old_oid):
            raise RuntimeError(f"Database object identity changed: {item['database']}")
        # Remove old cross-database grants, keeping the existing CONNECT convention.
        for grant in admin["instances"]:
            if grant["instance_id"] != instance["id"] and set(grant["privileges"]) - {"CONNECT"}:
                self.tw(self.base + f"/admins/{admin['id']}", "PATCH",
                        {"for_all": False, "instance_id": grant["instance_id"], "privileges": ["CONNECT"]})
                admin = self.wait_user(admin["id"])
        print(f"Database identity and objects verified: {item['login']} -> {item['database']}", flush=True)
        return admin

    def refresh_application(self, item, password):
        data = self.bao(item["secret_path"])["data"]
        connection = connection_credentials(data[CONNECTION_KEY], item["login"], password)
        self.merge_secret(item["secret_path"], {CONNECTION_KEY: connection})
        namespace, secret_name = item["namespace"], item["secret_name"]
        run(["kubectl", "-n", namespace, "annotate", "externalsecret", secret_name,
             f"force-sync={time.time_ns()}", "--overwrite"])
        for _ in range(30):
            secret = json.loads(run(["kubectl", "-n", namespace, "get", "secret", secret_name, "-o", "json"]))
            if base64.b64decode(secret["data"][CONNECTION_KEY]).decode() == connection:
                break
            time.sleep(3)
        else:
            raise RuntimeError(f"ExternalSecret did not synchronize: {namespace}")
        deployments = json.loads(run(["kubectl", "-n", namespace, "get", "deployment", "-o", "json"]))["items"]
        consumers = [d for d in deployments if any(ref.get("secretRef", {}).get("name") == secret_name
                     for c in d["spec"]["template"]["spec"]["containers"] for ref in c.get("envFrom", []))]
        if len(consumers) != 1:
            raise RuntimeError(f"Expected one application deployment: {namespace}")
        deployment = consumers[0]
        name = deployment["metadata"]["name"]
        run(["kubectl", "-n", namespace, "rollout", "restart", "deployment/" + name])
        run(["kubectl", "-n", namespace, "rollout", "status", "deployment/" + name, "--timeout=300s"])
        selector = ",".join(f"{k}={v}" for k, v in deployment["spec"]["selector"]["matchLabels"].items())
        pods = json.loads(run(["kubectl", "-n", namespace, "get", "pods", "-l", selector, "-o", "json"]))["items"]
        pods = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
        if len(pods) != deployment["spec"]["replicas"]:
            raise RuntimeError(f"Unexpected replica count: {namespace}")
        for pod in pods:
            env = run(["kubectl", "-n", namespace, "exec", pod["metadata"]["name"], "-c", "api", "--",
                       "cat", "/proc/1/environ"], binary=True)
            if (CONNECTION_KEY + "=" + connection).encode() not in env.split(b"\0"):
                raise RuntimeError(f"Application environment does not match OpenBao: {namespace}")
        if request(item["health_url"]) != "Healthy":
            raise RuntimeError(f"Application health check failed: {namespace}")
        print(f"Restart, process credentials and health verified: {namespace} ({len(pods)} replicas)", flush=True)

    def apply(self):
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise RuntimeError("Apply must run through the Managed PostgreSQL workflow")
        common = self.bao(COMMON_PATH)["data"]
        updates = {REVISION_KEY: self.config["password_revision"]}
        for item in self.config["users"]:
            prefix = item["prefix"]
            password = common.get(prefix + "_DB_PASSWORD")
            if common.get(REVISION_KEY) != self.config["password_revision"] or not password:
                password = "Aa1!" + secrets.token_urlsafe(18)
            updates[prefix + "_DB_USERNAME"] = item["login"]
            updates[prefix + "_DB_PASSWORD"] = password
        updates["DOTNET_TEMPLATE_DB_USERNAME"] = updates["DOTNET_TEMPLATE_DEV_DB_USERNAME"]
        updates["DOTNET_TEMPLATE_DB_PASSWORD"] = updates["DOTNET_TEMPLATE_DEV_DB_PASSWORD"]
        # Persist the password revision before API changes so a failed run can resume.
        self.merge_secret(COMMON_PATH, updates)
        clusters = self.tw("/k8s/clusters?limit=100")["clusters"]
        matches = [c for c in clusters if c["name"] == self.config["kubernetes_cluster_name"]]
        if len(matches) != 1:
            raise RuntimeError("Expected one Kubernetes cluster")
        with tempfile.TemporaryDirectory(prefix="application-postgres-users-") as temp:
            kubeconfig = Path(temp) / "kubeconfig"
            kubeconfig.write_text(self.tw(f"/k8s/clusters/{matches[0]['id']}/kubeconfig"))
            kubeconfig.chmod(0o600)
            os.environ["KUBECONFIG"] = str(kubeconfig)
            os.environ["KUBECTL_REMOTE_COMMAND_WEBSOCKETS"] = "false"
            for item in self.config["users"]:
                password = updates[item["prefix"] + "_DB_PASSWORD"]
                self.reconcile_user(item, password)
                self.refresh_application(item, password)
            verifier = self.config["users"][0]
            verifier_admin = {"login": verifier["login"], "password": updates[verifier["prefix"] + "_DB_PASSWORD"]}
            self.retire_users(verifier_admin, verifier["database"])

    def retire_users(self, verifier_admin, database):
        for admin in self.admins():
            if admin["login"] not in self.config["retired_users"]:
                continue
            # Default ACL records may be discarded. Never delete a table/schema owner.
            login = admin["login"].replace("'", "''")
            owned = self.sql(verifier_admin, database,
                             f"SELECT count(*) FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid=(SELECT oid FROM pg_roles WHERE rolname='{login}') AND deptype='o' AND classid<>'pg_default_acl'::regclass")
            if owned != "0":
                raise RuntimeError(f"Refusing to delete user that still owns objects: {admin['login']}")
            self.tw(self.base + f"/admins/{admin['id']}", "DELETE")
            self.wait_user(admin["id"], absent=True)
            remaining = self.sql(verifier_admin, database, f"SELECT count(*) FROM pg_roles WHERE rolname='{login}'")
            if remaining != "0":
                raise RuntimeError(f"Deleted Timeweb user still exists in PostgreSQL: {admin['login']}")
            print(f"Retired user removed: {admin['login']}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(__file__).with_name("application-postgres-users.json").read_text())
    reconciler = Reconciler(config)
    reconciler.plan()
    if args.apply:
        reconciler.apply()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, StopIteration, urllib.error.URLError) as error:
        raise SystemExit(f"Application PostgreSQL reconciliation failed: {error}") from None
