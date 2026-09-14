import importlib.util
import pathlib
import unittest
from unittest.mock import Mock, patch


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/timeweb/reconcile-application-postgres-users.py"
SPEC = importlib.util.spec_from_file_location("application_postgres_users", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ApplicationPostgresUsersTests(unittest.TestCase):
    def test_connection_rotation_preserves_database_and_tls_options(self):
        before = "Host=db;Port=5432;Database=prod;Username=old;Password=old-secret;SSL Mode=Require;Trust Server Certificate=true"
        result = MODULE.connection_credentials(before, "new_user", "Aa1!replacement")
        self.assertEqual(result, before.replace("Username=old", "Username=new_user")
                         .replace("Password=old-secret", "Password=Aa1!replacement"))

    def test_ambiguous_connection_credentials_are_rejected(self):
        for value in ("Host=db;Password=value", "Username=one;Username=two;Password=value"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                MODULE.connection_credentials(value, "new", "password")

    def test_existing_target_takes_precedence_over_retired_alias(self):
        target = {"login": "new"}
        self.assertIs(MODULE.select_user({"login": "new", "aliases": ["old"]},
                                        [{"login": "old"}, target]), target)

    def test_multiple_aliases_without_target_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            MODULE.select_user({"login": "new", "aliases": ["old", "older"], "database": "app"},
                               [{"login": "old"}, {"login": "older"}])

    def test_secret_merge_uses_cas_and_preserves_unrelated_settings(self):
        reconciler = object.__new__(MODULE.Reconciler)
        reconciler.bao_address = "https://bao.example"
        reconciler.bao_headers = {}
        expected = {"Connection": "new", "EmailTemplate": "keep", "Issuer": "keep"}
        reconciler.bao = Mock(side_effect=[
            {"data": expected | {"Connection": "old"}, "metadata": {"version": 9}},
            {"data": expected},
        ])
        with patch.object(MODULE, "request") as request:
            self.assertTrue(reconciler.merge_secret("app", {"Connection": "new"}))
        self.assertEqual(request.call_args.args[3], {"options": {"cas": 9}, "data": expected})

    def test_unchanged_secret_does_not_create_a_version(self):
        reconciler = object.__new__(MODULE.Reconciler)
        reconciler.bao = Mock(return_value={"data": {"Connection": "same"}})
        with patch.object(MODULE, "request") as request:
            self.assertFalse(reconciler.merge_secret("app", {"Connection": "same"}))
        request.assert_not_called()

    def test_owned_objects_block_retired_user_deletion(self):
        reconciler = object.__new__(MODULE.Reconciler)
        reconciler.config = {"retired_users": ["old"]}
        reconciler.admins = Mock(return_value=[{"id": 1, "login": "old"}])
        reconciler.sql = Mock(return_value="1")
        reconciler.tw = Mock()
        with self.assertRaisesRegex(RuntimeError, "still owns objects"):
            reconciler.retire_users({}, "app")
        reconciler.tw.assert_not_called()

    def test_retirement_verifies_actual_postgres_role_disappears(self):
        reconciler = object.__new__(MODULE.Reconciler)
        reconciler.base = "/databases/1"
        reconciler.config = {"retired_users": ["old"]}
        reconciler.admins = Mock(return_value=[{"id": 2, "login": "old"}, {"id": 3, "login": "keep"}])
        reconciler.sql = Mock(side_effect=["0", "1"])
        reconciler.tw = Mock()
        reconciler.wait_user = Mock()
        with self.assertRaisesRegex(RuntimeError, "still exists in PostgreSQL"):
            reconciler.retire_users({}, "app")
        reconciler.tw.assert_called_once_with("/databases/1/admins/2", "DELETE")

    def test_apply_requires_workflow_execution(self):
        reconciler = object.__new__(MODULE.Reconciler)
        with patch.dict(MODULE.os.environ, {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(RuntimeError, "workflow"):
                reconciler.apply()


if __name__ == "__main__":
    unittest.main()
