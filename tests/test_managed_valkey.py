import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error


SOURCE = Path(__file__).resolve().parents[1] / "scripts/timeweb/sync-managed-valkey.py"
SPEC = importlib.util.spec_from_file_location("managed_valkey", SOURCE)
valkey = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(valkey)


class ValkeyConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"id": "123", "network_id": "private-network", "host": "192.168.10.20"}
        self.db = {"id": 123, "type": "valkey9_1", "preset_id": 5067, "cpu": 1,
                   "ram": 2048, "disk": {"size": 10485760, "used": 0},
                   "availability_zone": "msk-1", "status": "started",
                   "is_enabled_public_network": False, "is_enabled_public_ipv6": False,
                   "is_autobackups_enabled": False, "is_backup_schedule_enabled": False,
                   "config_parameters": {"maxmemory-policy": "noeviction"},
                   "networks": [{"id": "private-network", "type": "local",
                                 "ips": [{"ip": "192.168.10.20"}]}]}

    def test_requested_configuration_passes(self):
        valkey.verify_database(self.db, {"is_enabled": False}, self.config)

    def test_public_access_or_paid_backup_flags_fail(self):
        for key in ("is_enabled_public_network", "is_enabled_public_ipv6",
                    "is_autobackups_enabled", "is_backup_schedule_enabled"):
            with self.subTest(key=key):
                db = {**self.db, key: True}
                with self.assertRaises(ValueError):
                    valkey.verify_database(db, {"is_enabled": False}, self.config)

    def test_public_address_is_rejected_even_when_access_flag_is_false(self):
        self.db["networks"].append({"type": "public", "ips": [{"ip": "203.0.113.10"}]})
        with self.assertRaisesRegex(ValueError, "public IP"):
            valkey.verify_database(self.db, {"is_enabled": False}, self.config)

    def test_wrong_network_or_address_is_rejected(self):
        for key, value in (("network_id", "another-network"), ("host", "192.168.10.21")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                valkey.verify_database(self.db, {"is_enabled": False}, {**self.config, key: value})

    def test_unsupported_version_or_larger_tariff_is_rejected(self):
        for key, value in (("type", "valkey8_1"), ("preset_id", 5075), ("ram", 4096),
                           ("disk", {"size": 20971520, "used": 0})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                valkey.verify_database({**self.db, key: value}, {"is_enabled": False}, self.config)

    def test_eviction_and_enabled_or_unknown_backup_schedule_are_rejected(self):
        db = copy.deepcopy(self.db)
        db["config_parameters"]["maxmemory-policy"] = "allkeys-lru"
        with self.assertRaisesRegex(ValueError, "eviction"):
            valkey.verify_database(db, {"is_enabled": False}, self.config)
        for backups in ({"is_enabled": True}, {}):
            with self.subTest(backups=backups), self.assertRaises(ValueError):
                valkey.verify_database(self.db, backups, self.config)


class ValkeyExporterCredentialsTests(unittest.TestCase):
    def test_provider_monitoring_credentials_are_published_separately_from_database_accounts(self):
        exporter = {"id": "a05c874e-65e8-40e9-9b30-adcb565c970b", "login": "admin", "password": "metrics-password"}
        with patch.object(valkey, "request_json", return_value={"exporters": [exporter]}) as request, \
                patch.object(valkey, "update_secret") as update, patch("builtins.print"):
            valkey.publish_exporter_credentials({"id": "123"}, {"Authorization": "provider"},
                                               "https://bao.invalid", {"X-Vault-Token": "bao"})
        self.assertEqual(request.call_args.args[0], "https://api.timeweb.cloud/api/v1/dbs/123/exporters")
        self.assertEqual(update.call_args.args, (
            "https://bao.invalid/v1/secret/data/core-platform/observability", {"X-Vault-Token": "bao"}, {
                "OBSERVABILITY_TIMEWEB_VALKEY_EXPORTER_ID": exporter["id"],
                "OBSERVABILITY_TIMEWEB_VALKEY_EXPORTER_USERNAME": "admin",
                "OBSERVABILITY_TIMEWEB_VALKEY_EXPORTER_PASSWORD": "metrics-password"}))

    def test_missing_or_ambiguous_replica_exporter_does_not_overwrite_credentials(self):
        for exporters in ([], [{}, {}]):
            with self.subTest(exporters=exporters), \
                    patch.object(valkey, "request_json", return_value={"exporters": exporters}), \
                    patch.object(valkey, "update_secret") as update, self.assertRaises(ValueError):
                valkey.publish_exporter_credentials({"id": "123"}, {}, "https://bao.invalid", {})
            update.assert_not_called()

    def test_incomplete_or_invalid_exporter_credentials_are_rejected(self):
        valid = {"id": "a05c874e-65e8-40e9-9b30-adcb565c970b", "login": "admin", "password": "password"}
        for key, value in (("id", "not-a-uuid"), ("password", ""), ("password", None), ("login", " ")):
            with self.subTest(key=key, value=value), \
                    patch.object(valkey, "request_json", return_value={"exporters": [{**valid, key: value}]}), \
                    patch.object(valkey, "update_secret") as update, self.assertRaises(ValueError):
                valkey.publish_exporter_credentials({"id": "123"}, {}, "https://bao.invalid", {})
            update.assert_not_called()


class ValkeyCredentialPublicationTests(unittest.TestCase):
    def test_new_secret_is_created_with_cas_zero_and_verified(self):
        desired = {"VALKEY_USERNAME": "envoy_gateway_user", "VALKEY_PASSWORD": "gateway-password"}
        missing = urllib.error.HTTPError("https://example.invalid", 404, "Not found", {}, None)
        with patch.object(valkey, "request_json", side_effect=[missing, {},
                {"data": {"data": desired}}]) as request:
            valkey.update_secret("https://example.invalid", {}, desired)
        self.assertEqual(request.call_args_list[1].args[2], {"options": {"cas": 0}, "data": desired})

    def test_permission_error_does_not_attempt_to_create_a_secret(self):
        forbidden = urllib.error.HTTPError("https://example.invalid", 403, "Forbidden", {}, None)
        with patch.object(valkey, "request_json", side_effect=forbidden) as request, \
                self.assertRaises(urllib.error.HTTPError):
            valkey.update_secret("https://example.invalid", {}, {"VALKEY_PASSWORD": "gateway-password"})
        self.assertEqual(request.call_count, 1)

    def test_cas_retry_preserves_concurrent_unrelated_credentials(self):
        desired = {"VALKEY_HOST": "192.168.10.20", "VALKEY_PASSWORD": "test-password"}
        conflict = urllib.error.HTTPError("https://example.invalid", 400, "CAS conflict", {}, None)
        responses = [{"data": {"metadata": {"version": 4}, "data": {"OTHER_PASSWORD": "old"}}},
                     conflict,
                     {"data": {"metadata": {"version": 5}, "data": {"OTHER_PASSWORD": "updated"}}}, {},
                     {"data": {"data": {"OTHER_PASSWORD": "updated", **desired}}}]
        with patch.object(valkey, "request_json", side_effect=responses) as request, \
                patch.object(valkey.time, "sleep"):
            valkey.update_secret("https://example.invalid", {}, desired)
        payload = request.call_args_list[-2].args[2]
        self.assertEqual(payload["options"]["cas"], 5)
        self.assertEqual(payload["data"]["OTHER_PASSWORD"], "updated")
        self.assertEqual(payload["data"]["VALKEY_PASSWORD"], "test-password")
        self.assertEqual(payload["data"]["VALKEY_HOST"], desired["VALKEY_HOST"])

    def test_unchanged_credentials_do_not_create_another_secret_version(self):
        current = {"VALKEY_HOST": "192.168.10.20", "VALKEY_PASSWORD": "test-password"}
        response = {"data": {"metadata": {"version": 6}, "data": current}}
        with patch.object(valkey, "request_json", return_value=response) as request:
            valkey.update_secret("https://example.invalid", {}, current)
        self.assertEqual(request.call_count, 1)

    def test_each_environment_receives_only_its_own_credentials(self):
        config = {"host": "192.168.10.20", "port": 6379, "users": {
            "development": "tactical_heroes_user_dev", "production": "tactical_heroes_user_prod"}}
        passwords = {"development": "dev-password", "production": "prod-password"}
        with patch.object(valkey, "update_secret") as update, patch("builtins.print"):
            valkey.publish_credentials(config, passwords, "https://example.invalid", {})
        self.assertEqual(update.call_count, 2)
        for call, environment in zip(update.call_args_list, config["users"]):
            self.assertTrue(call.args[0].endswith(f"/applications/tactical-heroes-api/{environment}"))
            self.assertEqual(call.args[2]["VALKEY_USERNAME"], config["users"][environment])
            self.assertEqual(call.args[2]["VALKEY_PASSWORD"], passwords[environment])

    def test_gateway_credentials_use_a_separate_platform_path(self):
        config = {"host": "192.168.10.20", "port": 6379,
                  "gateway": {"username": "envoy_gateway_user"}}
        with patch.object(valkey, "update_secret") as update, patch("builtins.print"):
            valkey.publish_gateway_credentials(config, "gateway-password", "https://example.invalid", {})
        update.assert_called_once_with("https://example.invalid/v1/secret/data/core-platform/envoy-gateway",
                                       {}, {"VALKEY_HOST": "192.168.10.20", "VALKEY_PORT": "6379",
                                            "VALKEY_USERNAME": "envoy_gateway_user",
                                            "VALKEY_PASSWORD": "gateway-password"})

    def test_bootstrap_cleanup_preserves_other_service_secrets(self):
        responses = [{"data": {"metadata": {"version": 6}, "data": {
            "VALKEY_PASSWORD": "old-password", "OTHER_PASSWORD": "keep"}}}, {},
            {"data": {"data": {"OTHER_PASSWORD": "keep"}}}]
        with patch.object(valkey, "request_json", side_effect=responses) as request:
            valkey.update_secret("https://example.invalid", {}, {}, remove=("VALKEY_PASSWORD",))
        self.assertEqual(request.call_args_list[1].args[2]["data"], {"OTHER_PASSWORD": "keep"})

    def test_failed_readback_is_not_reported_as_success(self):
        responses = [{"data": {"metadata": {"version": 6}, "data": {}}}, {},
                     {"data": {"data": {}}}]
        with patch.object(valkey, "request_json", side_effect=responses), \
                self.assertRaisesRegex(RuntimeError, "verification failed"):
            valkey.update_secret("https://example.invalid", {}, {"VALKEY_PASSWORD": "new-password"})


class ValkeyUserRetirementTests(unittest.TestCase):
    def setUp(self):
        self.config = {"id": "123", "retire_default_user": True,
                       "privileges": ["CONNECTION", "READ", "WRITE", "PUBSUB", "SCRIPTING", "TRANSACTION"],
                       "gateway": {"username": "envoy_gateway_user",
                                   "privileges": ["CONNECTION", "READ", "WRITE", "SCRIPTING"]},
                       "users": {"development": "tactical_heroes_user_dev",
                                 "production": "tactical_heroes_user_prod"}}
        self.users = [{"id": index, "login": login, "status": "created",
                       "instances": [{"privileges": self.config["privileges"]}]}
                      for index, login in enumerate(self.config["users"].values(), 1)]
        self.users.append({"id": 3, "login": "envoy_gateway_user", "status": "created",
                           "instances": [{"privileges": self.config["gateway"]["privileges"]}]})

    def test_missing_gateway_or_additional_gateway_grants_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "not ready: envoy_gateway_user"):
            valkey.verify_users(self.users[:-1], self.config)
        for grant in ("PUBSUB", "TRANSACTION", "DANGEROUS"):
            users = copy.deepcopy(self.users)
            users[-1]["instances"][0]["privileges"].append(grant)
            with self.subTest(grant=grant), self.assertRaisesRegex(RuntimeError, "privileges: envoy_gateway_user"):
                valkey.verify_users(users, self.config)

    def test_missing_application_user_prevents_default_deletion(self):
        response = {"admins": [{"id": 99, "login": "default"}, self.users[0]]}
        with patch.object(valkey, "request_json", return_value=response) as request, \
                self.assertRaisesRegex(RuntimeError, "not ready"):
            valkey.retire_default_user("https://example.invalid", {}, self.config)
        self.assertEqual(request.call_count, 1)

    def test_excessive_privileges_are_rejected(self):
        users = copy.deepcopy(self.users)
        users[0]["instances"][0]["privileges"].append("DANGEROUS")
        with self.assertRaisesRegex(RuntimeError, "privileges"):
            valkey.verify_users(users, self.config)

    def test_deletes_only_default_and_waits_for_provider_completion(self):
        before = {"admins": [{"id": 99, "login": "default"}, *self.users]}
        responses = [before, {}, before, {"admins": self.users}]
        with patch.object(valkey, "request_json", side_effect=responses) as request, \
                patch.object(valkey.time, "sleep"), patch("builtins.print"):
            valkey.retire_default_user("https://example.invalid", {}, self.config)
        self.assertEqual(request.call_args_list[1].args[0],
                         "https://example.invalid/databases/123/admins/99")
        self.assertEqual(request.call_args_list[1].kwargs, {"method": "DELETE"})

    def test_already_retired_user_is_not_deleted_again(self):
        with patch.object(valkey, "request_json", return_value={"admins": self.users}) as request, \
                patch("builtins.print"):
            valkey.retire_default_user("https://example.invalid", {}, self.config)
        self.assertFalse(any(call.kwargs.get("method") == "DELETE" for call in request.call_args_list))

    def test_failed_application_probe_prevents_retirement(self):
        env = {"VALKEY_CONFIG": json.dumps(self.config),
               "VALKEY_USER_PASSWORDS": json.dumps({"development": "d" * 30, "production": "p" * 30}),
               "VALKEY_DEFAULT_PASSWORD": "b" * 30, "VALKEY_GATEWAY_PASSWORD": "g" * 30,
               "TIMEWEB_TOKEN": "test-token"}
        with patch.dict(valkey.os.environ, env), \
                patch.object(valkey, "request_json", side_effect=[{"db": {}},
                    {"auto_backups_settings": {}}, {"admins": self.users}]), \
                patch.object(valkey, "verify_database"), \
                patch.object(valkey, "openbao_headers", return_value=("https://example.invalid", {})), \
                patch.object(valkey, "publish_credentials"), \
                patch.object(valkey, "publish_gateway_credentials"), \
                patch.object(valkey, "publish_exporter_credentials"), \
                patch.object(valkey, "probe_workers", side_effect=[None, RuntimeError("probe failed")]), \
                patch.object(valkey, "retire_default_user") as retire, patch("builtins.print"), \
                self.assertRaisesRegex(RuntimeError, "probe failed"):
            valkey.main()
        retire.assert_not_called()

    def test_retirement_is_independent_of_later_pubsub_check_but_cleanup_requires_revocation(self):
        env = {"VALKEY_CONFIG": json.dumps(self.config),
               "VALKEY_USER_PASSWORDS": json.dumps({"development": "d" * 30, "production": "p" * 30}),
               "VALKEY_DEFAULT_PASSWORD": "b" * 30, "VALKEY_GATEWAY_PASSWORD": "g" * 30,
               "TIMEWEB_TOKEN": "test-token"}
        cases = [([None, None, None, RuntimeError("default still works")], False),
                 ([None, None, None, None, None, RuntimeError("Pub/Sub blocked")], True)]
        for results, cleanup_expected in cases:
            with self.subTest(cleanup_expected=cleanup_expected), patch.dict(valkey.os.environ, env), \
                    patch.object(valkey, "request_json", side_effect=[{"db": {}},
                        {"auto_backups_settings": {}}, {"admins": self.users}]), \
                    patch.object(valkey, "verify_database"), \
                    patch.object(valkey, "openbao_headers", return_value=("https://example.invalid", {})), \
                    patch.object(valkey, "publish_credentials"), \
                    patch.object(valkey, "publish_gateway_credentials"), \
                    patch.object(valkey, "publish_exporter_credentials"), \
                    patch.object(valkey, "probe_workers", side_effect=results), \
                    patch.object(valkey, "retire_default_user") as retire, \
                    patch.object(valkey, "update_secret") as cleanup, patch("builtins.print"), \
                    self.assertRaises(RuntimeError):
                valkey.main()
            retire.assert_called_once()
            self.assertEqual(cleanup.called, cleanup_expected)

    def test_reusing_an_application_or_bootstrap_password_for_gateway_is_rejected(self):
        env = {"VALKEY_CONFIG": json.dumps(self.config),
               "VALKEY_USER_PASSWORDS": json.dumps({"development": "d" * 30, "production": "p" * 30}),
               "VALKEY_DEFAULT_PASSWORD": "b" * 30, "TIMEWEB_TOKEN": "test-token"}
        for duplicate in ("d" * 30, "p" * 30, "b" * 30):
            with self.subTest(duplicate=duplicate[0]), \
                    patch.dict(valkey.os.environ, {**env, "VALKEY_GATEWAY_PASSWORD": duplicate}), \
                    patch.object(valkey, "request_json") as request, \
                    self.assertRaisesRegex(ValueError, "independent passwords"):
                valkey.main()
            request.assert_not_called()


class ValkeyProbeWaitTests(unittest.TestCase):
    def test_waits_for_pod_success_after_container_termination(self):
        states = [{"phase": "Pending"},
                  {"phase": "Running", "containerStatuses": [{"state": {
                      "terminated": {"exitCode": 0, "reason": "Completed"}}}]},
                  {"phase": "Succeeded"}]
        responses = [SimpleNamespace(stdout=json.dumps({"status": status})) for status in states]
        with patch.object(valkey, "kubectl", side_effect=responses), \
                patch.object(valkey.time, "sleep") as sleep:
            result = valkey.wait_for_probe("default", "test-probe")
        self.assertEqual(result["phase"], "Succeeded")
        self.assertEqual(sleep.call_count, 2)

    def test_failed_pod_reports_immediately(self):
        response = SimpleNamespace(stdout=json.dumps({"status": {"phase": "Failed"}}))
        with patch.object(valkey, "kubectl", return_value=response), \
                patch.object(valkey.time, "sleep") as sleep:
            result = valkey.wait_for_probe("default", "test-probe")
        self.assertEqual(result["phase"], "Failed")
        sleep.assert_not_called()
