import copy
import importlib.util
from pathlib import Path
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


class ValkeyCredentialPublicationTests(unittest.TestCase):
    def test_cas_retry_preserves_concurrent_unrelated_credentials(self):
        config = {"host": "192.168.10.20", "port": 6379, "username": "default"}
        conflict = urllib.error.HTTPError("https://example.invalid", 400, "CAS conflict", {}, None)
        responses = [{"value": "test-jwt"}, {"auth": {"client_token": "test-token"}},
                     {"data": {"metadata": {"version": 4}, "data": {"OTHER_PASSWORD": "old"}}},
                     conflict,
                     {"data": {"metadata": {"version": 5}, "data": {"OTHER_PASSWORD": "updated"}}}, {}]
        with patch.dict(valkey.os.environ, {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.invalid?x=1",
                                           "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-request-token"}), \
                patch.object(valkey, "request_json", side_effect=responses) as request, \
                patch.object(valkey.time, "sleep"), patch("builtins.print"):
            valkey.publish_credentials(config, "test-password")
        payload = request.call_args_list[-1].args[2]
        self.assertEqual(payload["options"]["cas"], 5)
        self.assertEqual(payload["data"]["OTHER_PASSWORD"], "updated")
        self.assertEqual(payload["data"]["VALKEY_PASSWORD"], "test-password")
        self.assertEqual(payload["data"]["VALKEY_HOST"], config["host"])

    def test_unchanged_credentials_do_not_create_another_secret_version(self):
        config = {"host": "192.168.10.20", "port": 6379, "username": "default"}
        current = {"VALKEY_HOST": config["host"], "VALKEY_PORT": "6379",
                   "VALKEY_USERNAME": "default", "VALKEY_PASSWORD": "test-password"}
        responses = [{"value": "test-jwt"}, {"auth": {"client_token": "test-token"}},
                     {"data": {"metadata": {"version": 6}, "data": current}}]
        with patch.dict(valkey.os.environ, {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.invalid?x=1",
                                           "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-request-token"}), \
                patch.object(valkey, "request_json", side_effect=responses) as request, \
                patch("builtins.print"):
            valkey.publish_credentials(config, "test-password")
        self.assertEqual(request.call_count, 3)
