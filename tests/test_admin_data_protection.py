import json
import os
import pathlib
import subprocess
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/timeweb/reconcile-managed-postgres.sh"


class AdminDataProtectionSecretTests(unittest.TestCase):
    def reconcile(self, existing):
        source = SCRIPT.read_text()
        start = source.index("for environment in development production; do")
        end = source.index("\ndone", start) + len("\ndone")
        program = """set -euo pipefail
bao_read_optional() { printf '%s' "$EXISTING_CONFIG"; }
bao_write() { jq -nc --arg path "$2" --argjson data "$3" '{path: $path, data: $data}'; }
openbao_token=test-token
tactical_heroes_dev_connection_string='Host=postgres.internal;Port=5432;Database=tactical_heroes_dev;Username=api_dev;Password=synthetic-dev-password;SSL Mode=Require;Trust Server Certificate=true;GSS Encryption Mode=Disable'
tactical_heroes_prod_connection_string='Host=postgres.internal;Port=5432;Database=tactical_heroes_prod;Username=api_prod;Password=synthetic-prod-password;SSL Mode=Require;Trust Server Certificate=true;GSS Encryption Mode=Disable'
""" + source[start:end]
        return subprocess.run(
            ["bash", "-c", program],
            env={**os.environ, "EXISTING_CONFIG": existing},
            capture_output=True, text=True, timeout=10,
        )

    def test_preserves_existing_settings_and_reuses_matching_api_credentials(self):
        existing = {"Oidc__ClientSecret": "synthetic-oidc-secret",
                    "ConnectionStrings__DataProtection": "old-connection"}

        result = self.reconcile(json.dumps(existing))

        self.assertEqual(result.returncode, 0, result.stderr)
        writes = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(writes), 2)
        for write, environment, suffix in zip(writes, ("development", "production"), ("dev", "prod")):
            self.assertEqual(write["path"], f"applications/tactical-heroes-admin/{environment}")
            self.assertEqual(write["data"]["Oidc__ClientSecret"], existing["Oidc__ClientSecret"])
            connection = write["data"]["ConnectionStrings__DataProtection"]
            self.assertEqual(connection,
                             f"Host=postgres.internal;Port=5432;Database=tactical_heroes_{suffix};"
                             f"Username=api_{suffix};Password=synthetic-{suffix}-password;"
                             "SSL Mode=Require;Trust Server Certificate=true;GSS Encryption Mode=Disable")

    def test_initial_provisioning_creates_both_application_secrets(self):
        result = self.reconcile("{}")

        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            self.assertEqual(set(json.loads(line)["data"]), {"ConnectionStrings__DataProtection"})

    def test_invalid_existing_secret_stops_before_any_write(self):
        result = self.reconcile("invalid-json")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_reconciliation_preserves_the_generated_connection_strings(self):
        first = self.reconcile("{}")
        self.assertEqual(first.returncode, 0, first.stderr)
        for line in first.stdout.splitlines():
            initial = json.loads(line)
            repeated = self.reconcile(json.dumps(initial["data"]))
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            matching = next(json.loads(item) for item in repeated.stdout.splitlines()
                            if json.loads(item)["path"] == initial["path"])
            self.assertEqual(matching, initial)


if __name__ == "__main__":
    unittest.main()
