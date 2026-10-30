"""Release selection, failure isolation and durable notification deduplication."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "kubernetes/charts/core-platform-workloads/files"
SPEC = importlib.util.spec_from_file_location("releases", FILES / "infrastructure_releases.py")
releases = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(releases)
CATALOG = json.loads((FILES / "infrastructure-releases.json").read_text(encoding="utf-8"))
START = 1_800_000_000


def pod(image="grafana/grafana:13.2.3", namespace="observability", **metadata):
    return {"metadata": {"namespace": namespace, "labels": {}, **metadata},
            "spec": {"containers": [{"name": "app", "image": image}]},
            "status": {"phase": "Running", "containerStatuses": [{"name": "app", "image": image, "imageID": "sha256:abc"}]}}


class FakeClient:
    def __init__(self):
        self.pods = [pod()]
        self.release = {"version": "13.3.0", "url": "https://github.com/grafana/grafana/releases/tag/v13.3.0"}
        self.failure = False
        self.calls = 0

    def inventory(self):
        return self.pods

    def latest(self, _):
        self.calls += 1
        if self.failure:
            raise TimeoutError("secret must never be logged")
        return self.release


class VersionTests(unittest.TestCase):
    def test_numeric_order_and_provider_build(self):
        self.assertGreater(releases.version("1.10.0"), releases.version("1.9.99"))
        self.assertEqual(releases.version("1.37"), releases.version("1.37.0"))
        self.assertGreater(releases.version("1.36.4+k0s.1"), releases.version("1.36.4+k0s.0"))
        for value in ("latest", "v1.2.3", "1.2.3-rc1", "1.2.3beta", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                releases.version(value)

    def test_github_stable_selection_is_not_publication_order(self):
        client = releases.Client()
        fixture = [{"tag_name": tag, "draft": draft, "prerelease": pre}
                   for tag, draft, pre in [("v13.2.4", False, False), ("v14.0.0", False, True),
                                           ("v15.0.0", True, False), ("v13.3.1", False, False),
                                           ("v16.0.0-rc1", False, False)]]
        with patch.object(client, "get", side_effect=lambda url: fixture[0] if url.endswith('/latest') else fixture):
            self.assertEqual(client.latest({"source": {"kind": "github", "repo": "grafana/grafana"}})["version"], "13.3.1")

    def test_sonar_server_and_chart_tags_do_not_cross_channels(self):
        client = releases.Client()
        data = [{"tag_name": tag, "draft": False, "prerelease": False}
                for tag in ("2026.4.0", "26.9.0.129388", "26.10.0.130000", "26.11.0.130001-rc1")]
        sonar = next(c for c in CATALOG["components"] if c["name"] == "SonarQube Community Build")
        with patch.object(client, "get", side_effect=lambda url: data[0] if url.endswith('/latest') else data):
            self.assertEqual(client.latest(sonar)["version"], "26.10.0.130000")
        chart = next(c for c in CATALOG["components"] if c["name"] == "Argo CD chart")
        data = [{"tag_name": tag, "draft": False, "prerelease": False}
                for tag in ("argo-workflows-99.0.0", "argo-cd-10.10.2", "argo-cd-10.9.9")]
        with patch.object(client, "get", side_effect=lambda url: data[0] if url.endswith('/latest') else data):
            self.assertEqual(client.latest(chart)["version"], "10.10.2")

    def test_docker_unstable_alias_and_python_prerelease_excluded(self):
        client = releases.Client()
        busybox = next(c for c in CATALOG["components"] if c["name"] == "BusyBox")
        data = {"results": [{"name": "unstable", "digest": "new"}, {"name": "1.38.0", "digest": "new"},
                            {"name": "1.37.0", "digest": "stable"}]}
        with patch.object(client, "get", return_value=data):
            self.assertEqual(client.latest(busybox)["version"], "1.37.0")
        python = next(c for c in CATALOG["components"] if c["name"] == "Python helpers")
        data = {"results": [{"name": "3.15.0rc3-alpine"}, {"name": "3.14.8-alpine"}, {"name": "3.13.16-alpine"}]}
        with patch.object(client, "get", return_value=data):
            self.assertEqual(client.latest(python)["version"], "3.14.8")

    def test_timeweb_versions_include_build_and_exclude_deprecated_database(self):
        client = releases.Client()
        k8s = next(c for c in CATALOG["components"] if c["name"] == "Kubernetes (Timeweb)")
        with patch.object(client, "timeweb", return_value={"k8s_versions": ["v1.36.4+k0s.0", "v1.36.4+k0s.1", "v1.37.0-rc.1+k0s.0"]}):
            self.assertEqual(client.latest(k8s)["version"], "1.36.4+k0s.1")
        db = next(c for c in CATALOG["components"] if c["name"] == "PostgreSQL (Timeweb)")
        data = {"types": [{"name": "postgresql", "version": "18", "is_deprecated": False},
                          {"name": "postgresql", "version": "19", "is_deprecated": True},
                          {"name": "mysql", "version": "99", "is_deprecated": False}]}
        with patch.object(client, "timeweb", return_value=data):
            self.assertEqual(client.latest(db)["version"], "18")

    def test_empty_release_response_fails_closed(self):
        client = releases.Client()
        with patch.object(client, "get", side_effect=lambda url: {"tag_name":"nightly", "draft":False, "prerelease":True} if url.endswith('/latest') else []), self.assertRaises(ValueError):
            client.latest(CATALOG["components"][0])

    def test_timeweb_valkey_selects_only_available_stable_versions(self):
        component = next(c for c in CATALOG["components"] if c["name"] == "Valkey (Timeweb)")
        client = releases.Client()
        data = {"types": [
            {"name": "valkey", "version": value, "is_deprecated": deprecated}
            for value, deprecated in [("7", False), ("8.1", False), ("9.9", False),
                                      ("9.10", False), ("10", True), ("11.0-rc.1", False)]] + [
            {"name": "postgresql", "version": "99", "is_deprecated": False},
            {"name": "redis", "version": "100", "is_deprecated": False}]}
        with patch.object(client, "timeweb", return_value=data) as api:
            self.assertEqual(client.latest(component), {
                "version": "9.10", "url": "https://timeweb.cloud/docs/dbaas/valkey"})
        api.assert_called_once_with("database-types")

    def test_timeweb_valkey_without_available_versions_fails_closed(self):
        component = next(c for c in CATALOG["components"] if c["name"] == "Valkey (Timeweb)")
        client = releases.Client()
        for types in ([], [{"name": "valkey", "version": "9.1", "is_deprecated": True}],
                      [{"name": "postgresql", "version": "18", "is_deprecated": False}]):
            with self.subTest(types=types), patch.object(client, "timeweb", return_value={"types": types}), self.assertRaises(ValueError):
                client.latest(component)

    def test_credentials_cannot_be_forwarded_to_other_hosts(self):
        client = releases.Client()
        for auth in ("timeweb", "kubernetes"):
            with self.subTest(auth=auth), self.assertRaises(ValueError):
                client.get("https://example.com/", auth)

    def test_transient_http_failure_retries_but_rate_limit_waits_for_next_cycle(self):
        opener = MagicMock()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        opener.open.side_effect = [urllib.error.HTTPError("https://example.com", 503, "unavailable", {}, None), response]
        with patch.object(releases.urllib.request, "build_opener", return_value=opener), patch.object(releases.time, "sleep"):
            self.assertEqual(releases.Client().get("https://example.com"), {"ok": True})
        self.assertEqual(opener.open.call_count, 2)
        opener.open.reset_mock()
        opener.open.side_effect = urllib.error.HTTPError("https://example.com", 429, "limited", {}, None)
        with patch.object(releases.urllib.request, "build_opener", return_value=opener), self.assertRaises(urllib.error.HTTPError):
            releases.Client().get("https://example.com")
        self.assertEqual(opener.open.call_count, 1)


class InventoryTests(unittest.TestCase):
    def test_unpulled_desired_image_is_not_an_installed_upgrade(self):
        component = next(c for c in CATALOG["components"] if c["name"] == "Grafana")
        item = pod("grafana/grafana:13.3.0")
        item["status"]["containerStatuses"][0]["imageID"] = ""
        self.assertEqual(releases.deployed(component, [item]), [])
        item["status"]["containerStatuses"][0].update(image="grafana/grafana:13.2.3", imageID="sha256:abc")
        self.assertEqual(releases.deployed(component, [item]), ["13.2.3"])

    def test_digest_only_containerd_status_retains_pinned_version(self):
        component = next(c for c in CATALOG["components"] if c["name"] == "Python helpers")
        item = pod("python:3.14.8-alpine@sha256:" + "b" * 64)
        item["status"]["containerStatuses"][0]["image"] = "sha256:" + "a" * 64
        self.assertEqual(releases.deployed(component, [item]), ["3.14.8"])

    def test_mixed_rollout_init_images_digest_and_finished_pods(self):
        component = next(c for c in CATALOG["components"] if c["name"] == "Grafana")
        pods = [pod(), pod("grafana/grafana:13.3.0@sha256:abc"), pod("grafana/grafana:10.0.0", deletionTimestamp="now")]
        finished = pod("grafana/grafana:1.0.0")
        finished["status"]["phase"] = "Succeeded"
        pods.append(finished)
        self.assertEqual(releases.deployed(component, pods), ["13.2.3", "13.3.0"])
        pods.append(pod("grafana/grafana:latest"))
        with self.assertRaises(ValueError):
            releases.deployed(component, pods)

    def test_chart_labels_and_namespace(self):
        component = next(c for c in CATALOG["components"] if c["name"] == "Argo CD chart")
        good = pod(namespace="argocd", labels={"helm.sh/chart": "argo-cd-10.9.6"})
        other = pod(namespace="other", labels={"helm.sh/chart": "argo-cd-99.0.0"})
        self.assertEqual(releases.deployed(component, [good, other]), ["10.9.6"])

    def test_provider_own_and_unknown_images_are_visible(self):
        pods = [pod("provider/cilium:v1.20.0", "kube-system"), pod("ghcr.io/panixida-infrastructure/test:v1"),
                pod("unknown/component:latest"), pod("user/application:production-3", "tactical-heroes-production")]
        rows = releases.inventory_only(pods, CATALOG["components"], CATALOG)
        self.assertEqual({r["status"] for r in rows}, {"provider_managed", "own_build", "untracked"})
        self.assertEqual(len(rows), 3)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state.json"
        self.config = copy.deepcopy(CATALOG)
        self.config["components"] = [next(c for c in CATALOG["components"] if c["name"] == "Grafana")]
        self.client = FakeClient()
        self.monitor = releases.Monitor(self.config, self.path)

    def test_timeweb_databases_use_separate_clusters_and_emit_only_new_versions(self):
        self.config.update(databaseClusterId=123, valkeyClusterId=456)
        self.config["components"] = [c for c in CATALOG["components"]
                                     if c["name"] in ("PostgreSQL (Timeweb)", "Valkey (Timeweb)")]
        responses = {
            "dbs/123": {"db": {"type": "postgresql18"}},
            "dbs/456": {"db": {"type": "valkey9_1"}},
            "database-types": {"types": [
                {"name": "postgresql", "type": "postgresql18", "version": "18", "is_deprecated": False},
                {"name": "valkey", "type": "valkey9_1", "version": "9.1", "is_deprecated": False}]},
        }
        client = releases.Client()
        with patch.object(client, "inventory", return_value=[]), patch.object(client, "timeweb", side_effect=responses.__getitem__) as api:
            self.monitor.poll(client, START)
            self.assertEqual([(r["current_version"], r["latest_version"], r["status"]) for r in self.monitor.rows],
                             [("18", "18", "current"), ("9.1", "9.1", "current")])
            self.assertEqual(self.monitor.events, [])
            self.assertIn(unittest.mock.call("dbs/456"), api.call_args_list)
            responses["database-types"]["types"][1]["is_deprecated"] = True
            responses["database-types"]["types"].append({
                "name": "valkey", "type": "valkey9_2", "version": "9.2", "is_deprecated": False})
            self.monitor.poll(client, START + releases.DAY)
            self.assertEqual(len(self.monitor.events), 1)
            event = self.monitor.events[0]
            self.assertEqual((event["component"], event["current_version"], event["latest_version"]),
                             ("Valkey (Timeweb)", "9.1", "9.2"))
            restarted = releases.Monitor(self.config, self.path)
            restarted.poll(client, START + releases.DAY + releases.EVENT_WINDOW + 1)
            self.assertEqual(restarted.events, [])
            self.assertEqual(restarted.rows[1]["status"], "update_available")

    def test_timeweb_valkey_unknown_or_wrong_engine_type_is_not_current(self):
        self.config["valkeyClusterId"] = 456
        self.config["components"] = [c for c in CATALOG["components"] if c["name"] == "Valkey (Timeweb)"]
        client = releases.Client()
        for engine, database_type, value in (("valkey", "unknown", "9.1"),
                                            ("postgresql", "postgresql18", "18"),
                                            ("valkey", "valkey9_1", "unrecognized")):
            responses = {
                "dbs/456": {"db": {"type": database_type}},
                "database-types": {"types": [
                    {"name": engine, "type": "postgresql18" if engine == "postgresql" else "valkey9_1",
                     "version": value, "is_deprecated": False}]},
            }
            with self.subTest(engine=engine, database_type=database_type), patch.object(client, "inventory", return_value=[]), patch.object(client, "timeweb", side_effect=responses.__getitem__), patch("builtins.print"):
                self.monitor.poll(client, START)
            self.assertEqual(self.monitor.rows[0]["status"], "inventory_failed")
            self.assertEqual(self.monitor.events, [])

    def test_daily_check_and_restart_do_not_repeat_event(self):
        self.monitor.poll(self.client, START)
        self.assertEqual(len(self.monitor.events), 1)
        self.monitor.poll(self.client, START + 300)
        self.assertEqual(self.client.calls, 1)
        restarted = releases.Monitor(self.config, self.path)
        restarted.poll(self.client, START + 3601)
        self.assertEqual(restarted.events, [])
        restarted.poll(self.client, START + releases.DAY)
        self.assertEqual(self.client.calls, 2)
        self.assertEqual(restarted.events, [])
        self.assertEqual(restarted.rows[0]["status"], "update_available")

    def test_next_release_notifies_but_rollbacks_do_not(self):
        self.monitor.poll(self.client, START)
        self.client.release = {"version": "13.4.0", "url": "https://github.com/grafana/grafana/releases/tag/v13.4.0"}
        self.monitor.poll(self.client, START + releases.DAY)
        self.assertEqual(self.monitor.events[0]["latest_version"], "13.4.0")
        self.client.pods = [pod("grafana/grafana:13.4.0")]
        self.monitor.poll(self.client, START + releases.DAY + 300)
        self.assertEqual(self.monitor.events, [])
        self.assertEqual(self.monitor.rows[0]["status"], "current")
        self.client.pods = [pod("grafana/grafana:13.2.3")]
        self.monitor.poll(self.client, START + 2 * releases.DAY)
        self.assertEqual(self.monitor.events, [])

    def test_event_labels_stay_stable_during_mixed_rollout(self):
        self.monitor.poll(self.client, START)
        self.client.pods.append(pod("grafana/grafana:13.3.0"))
        self.monitor.poll(self.client, START + 300)
        self.assertEqual(self.monitor.events[0]["current_version"], "13.2.3")

    def test_failed_check_preserves_cache_and_is_not_green(self):
        self.monitor.poll(self.client, START)
        self.client.failure = True
        with patch("builtins.print") as logger:
            self.monitor.poll(self.client, START + releases.DAY)
            self.assertNotIn("secret", str(logger.call_args))
        self.assertEqual(self.monitor.rows[0]["status"], "check_failed")
        self.assertEqual(self.monitor.rows[0]["latest_version"], "13.3.0")
        self.assertEqual(self.monitor.rows[0]["last_success"], START)
        self.assertEqual(self.monitor.events, [])
        self.monitor.poll(self.client, START + releases.DAY + 300)
        self.assertEqual(self.client.calls, 2)
        self.client.failure = False
        self.monitor.poll(self.client, START + releases.DAY + 3600)
        self.assertEqual(self.client.calls, 3)
        self.assertEqual(self.monitor.rows[0]["status"], "update_available")
        self.assertEqual(self.monitor.events, [])

    def test_inventory_failure_suppresses_events_and_marks_rows_unknown(self):
        self.monitor.poll(self.client, START)
        with patch.object(self.client, "inventory", side_effect=TimeoutError()), patch("builtins.print"):
            self.monitor.poll(self.client, START + 300)
        self.assertEqual(self.monitor.inventory_success, 0)
        self.assertIn('status="inventory_failed"', self.monitor.render())
        self.assertNotIn("infrastructure_release_new_version", self.monitor.render())

    def test_state_write_failure_does_not_emit_or_acknowledge_events(self):
        with patch.object(self.monitor, "save", side_effect=OSError()), patch("builtins.print"):
            self.monitor.poll(self.client, START)
        self.assertEqual(self.monitor.events, [])
        self.assertEqual(self.monitor.state["components"], {})
        self.assertEqual(self.monitor.state_success, 0)
        self.monitor.poll(self.client, START + 300)
        self.assertEqual(len(self.monitor.events), 1)

    def test_corrupt_state_does_not_silently_resend_everything(self):
        self.path.write_text('{"schema":9}', encoding="utf-8")
        with self.assertRaises(ValueError):
            releases.Monitor(self.config, self.path)

    def test_uninstalled_component_is_not_update_available(self):
        self.client.pods = []
        self.monitor.poll(self.client, START)
        self.assertEqual(self.monitor.rows[0]["status"], "not_installed")
        self.assertEqual(self.client.calls, 0)
        self.assertEqual(self.monitor.events, [])

    def test_failed_source_does_not_block_other_components(self):
        self.config["components"].append(next(c for c in CATALOG["components"] if c["name"] == "Keycloak"))
        self.client.pods.append(pod("quay.io/keycloak/keycloak:26.8.0", "identity"))
        def latest(component):
            if component["name"] == "Grafana":
                raise TimeoutError()
            return {"version": "26.9.0", "url": "https://github.com/keycloak/keycloak/releases/tag/26.9.0"}
        with patch.object(self.client, "latest", side_effect=latest), patch("builtins.print"):
            self.monitor.poll(self.client, START)
        self.assertEqual([r["status"] for r in self.monitor.rows], ["check_failed", "update_available"])
        self.assertEqual(len(self.monitor.events), 1)

    def test_metric_labels_escape_untrusted_text(self):
        self.monitor.poll(self.client, START)
        self.monitor.rows[0]["note"] = 'quote" slash\\ newline\n'
        self.assertIn('quote\\" slash\\\\ newline\\n', self.monitor.render())


if __name__ == "__main__":
    unittest.main()
