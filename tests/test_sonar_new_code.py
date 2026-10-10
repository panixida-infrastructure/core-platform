import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/sonarqube/reconcile-repositories.sh'


class SonarNewCodeTests(unittest.TestCase):
    def test_project_policy_replaces_old_branch_overrides_and_enables_comments(self):
        source = SCRIPT.read_text()
        start = source.index('  policy_arguments=(')
        end = source.index('\n  curl -fsS', source.index('summaryCommentEnabled=true', start))
        for policy in ('NUMBER_OF_DAYS', 'PREVIOUS_VERSION'):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as directory:
                log = pathlib.Path(directory) / 'calls'
                program = '''set -euo pipefail
curl() {
  printf '%s\\n' "$*" >> "$CALL_LOG"
  if [[ "$*" == *api/project_branches/list* ]]; then
    printf '%s' '{"branches":[{"name":"main"},{"name":"development"}]}'
  fi
}
project_key=project
repository=owner/repo
sonar_url=https://sonar.example.test
sonar_admin_password=test
sonar_github_integration_key=GitHub
''' + source[start:end]
                result = subprocess.run(['bash', '-c', program], capture_output=True, text=True,
                                        env={**os.environ, 'new_code_type': policy, 'CALL_LOG': str(log)}, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = log.read_text().splitlines()
                self.assertIn(f'type={policy}', calls[0])
                self.assertEqual('value=30' in calls[0], policy == 'NUMBER_OF_DAYS')
                resets = [call for call in calls if 'api/new_code_periods/unset' in call]
                self.assertEqual(len(resets), 2)
                self.assertTrue(any('branch=main' in call for call in resets))
                self.assertTrue(any('branch=development' in call for call in resets))
                self.assertIn('summaryCommentEnabled=true', calls[-1])

    def test_inventory_rejects_missing_or_unknown_policy(self):
        inventory = json.loads((ROOT / 'inventory/sonarqube/repositories.json').read_text())
        for policy in (None, 'REFERENCE_BRANCH', 'NUMBER_OF_DAYS'):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as directory:
                entry = {**inventory['repositories'][0]}
                if policy is None:
                    entry.pop('newCode')
                else:
                    entry['newCode'] = policy
                file = pathlib.Path(directory) / 'inventory.json'
                file.write_text(json.dumps({'repositories': [entry]}))
                result = subprocess.run(['bash', str(ROOT / 'scripts/sonarqube/validate-repositories.sh'), str(file)],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode == 0, policy == 'NUMBER_OF_DAYS', result.stderr)

    def test_disabling_plugin_preserves_other_plugins_on_persistent_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            plugins = pathlib.Path(directory) / 'plugins'
            plugins.mkdir()
            branch = plugins / 'sonarqube-community-branch-plugin-26.9.0-SNAPSHOT.jar'
            branch.write_text('plugin')
            other = plugins / 'other.jar'
            other.write_text('unrelated')
            result = subprocess.run(['sh', str(ROOT / 'kubernetes/charts/core-platform-workloads/files/sonarqube-branch-plugin.sh')],
                                    capture_output=True, text=True,
                                    env={**os.environ, 'BRANCH_PLUGIN_ENABLED': 'false',
                                         'SONAR_PLUGIN_DIRECTORY': str(plugins)}, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(branch.exists())
            self.assertEqual(other.read_text(), 'unrelated')
            self.assertEqual((plugins.parent / 'disabled-plugins' / f'{branch.name}.disabled').read_text(), 'plugin')


if __name__ == '__main__':
    unittest.main()
