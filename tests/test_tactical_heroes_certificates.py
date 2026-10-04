import json
import os
import pathlib
import subprocess
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/timeweb/reconcile-managed-postgres.sh"


class TacticalHeroesCertificatePreservationTests(unittest.TestCase):
    def reconcile(self, existing):
        source = SCRIPT.read_text()
        start = source.index('tactical_heroes_dev_app_secret="$(jq -n')
        end = source.index('\nbao_write "$openbao_token" core-platform/identity', start)
        program = """set -euo pipefail
bao_read_optional() { printf '%s' "$EXISTING_CONFIG"; }
openbao_token=test-token
tactical_heroes_common_app_config='{"Identity__Provider__Audience":"tactical-heroes-api"}'
tactical_heroes_dev_connection_string=development-db
tactical_heroes_prod_connection_string=production-db
tactical_heroes_dev_client_secret=new-dev-secret
tactical_heroes_prod_client_secret=new-prod-secret
tactical_heroes_smtp_password=new-smtp-password
""" + source[start:end] + """
jq -n --argjson dev "$tactical_heroes_dev_app_secret" --argjson prod "$tactical_heroes_prod_app_secret" '{development: $dev, production: $prod}'
"""
        return subprocess.run(
            ["bash", "-c", program],
            env={**os.environ, "EXISTING_CONFIG": existing},
            capture_output=True, text=True, timeout=10,
        )

    def test_preserves_current_and_rotating_certificates_while_updating_managed_values(self):
        certificates = {
            f"Identity__Provider__{purpose}Certificates__{index}__{field}":
                f"test-{purpose}-{index}-{field}"
            for purpose in ("Signing", "Encryption")
            for index in (0, 1)
            for field in ("PfxBase64", "Password")
        }
        existing = {
            **certificates,
            "ConnectionStrings__PostgreSqlConnectionString": "old-db",
            "Identity__Provider__Clients__1__ClientSecret": "old-client-secret",
            "UnrelatedField": "not-preserved",
        }

        result = self.reconcile(json.dumps(existing))

        self.assertEqual(result.returncode, 0, result.stderr)
        for environment, config in json.loads(result.stdout).items():
            for key, value in certificates.items():
                self.assertEqual(config[key], value)
            self.assertEqual(config["ConnectionStrings__PostgreSqlConnectionString"], f"{environment}-db")
            self.assertNotEqual(config["Identity__Provider__Clients__1__ClientSecret"], "old-client-secret")
            self.assertNotIn("UnrelatedField", config)

    def test_allows_initial_provisioning_without_certificates(self):
        result = self.reconcile("{}")

        self.assertEqual(result.returncode, 0, result.stderr)
        for config in json.loads(result.stdout).values():
            self.assertFalse(any("Certificates__" in key for key in config))

    def test_stops_when_existing_secret_cannot_be_parsed(self):
        result = self.reconcile("invalid-json")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
