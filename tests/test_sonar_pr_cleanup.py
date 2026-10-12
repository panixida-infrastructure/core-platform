import contextlib
import importlib.util
import io
import json
import pathlib
import unittest
import urllib.error
from unittest.mock import Mock, call, patch


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'kubernetes/charts/core-platform-workloads/files/sonarqube_pr_cleanup.py'
SPEC = importlib.util.spec_from_file_location('sonar_pr_cleanup', SCRIPT)
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


class CleanupRequestTests(unittest.TestCase):
    def response(self, body=b'{"token":"private-token"}'):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = body
        return response

    def http_error(self, code, headers=None):
        return urllib.error.HTTPError('https://api.github.com', code, 'private-body', headers or {}, io.BytesIO(b'private-body'))

    def test_token_creation_recovers_after_transient_server_error(self):
        github = cleanup.GitHub('https://api.github.com', 'app', 'unused')
        with patch.object(github, 'app_jwt', return_value='private-jwt'), \
                patch.object(cleanup.urllib.request, 'urlopen', side_effect=[
                    self.response(b'{"id":123}'), self.http_error(500), self.response(),
                ]) as urlopen, patch.object(cleanup.time, 'sleep') as sleep, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(github.token('owner/repo'), 'private-token')
            self.assertEqual(github.token('owner/repo'), 'private-token')
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(urlopen.call_args.args[0].method, 'POST')
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)['permissions'],
                         {'contents': 'read', 'pull_requests': 'read'})
        sleep.assert_called_once_with(2)
        event = json.loads(output.getvalue())
        self.assertEqual(event['level'], 'warning')
        self.assertEqual(event['alert_owner'], 'core-platform')
        self.assertNotIn('private-', output.getvalue())

    def test_persistent_server_errors_stop_after_four_attempts(self):
        with patch.object(cleanup.urllib.request, 'urlopen', side_effect=[self.http_error(503) for _ in range(4)]) as urlopen, \
                patch.object(cleanup.time, 'sleep') as sleep, patch.object(cleanup, 'log'):
            with self.assertRaisesRegex(cleanup.ApiError, 'HTTP 503'):
                cleanup.request('https://api.github.com', '/test', 'private-token', retry=True)
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(2), call(4), call(8)])

    def test_client_errors_are_not_retried(self):
        for code in (401, 403, 404, 422, 429):
            with self.subTest(code=code), \
                    patch.object(cleanup.urllib.request, 'urlopen', side_effect=self.http_error(code)) as urlopen, \
                    patch.object(cleanup.time, 'sleep') as sleep:
                with self.assertRaisesRegex(cleanup.ApiError, f'HTTP {code}'):
                    cleanup.request('https://api.github.com', '/test', 'unused', retry=True)
                urlopen.assert_called_once()
                sleep.assert_not_called()

    def test_sonar_deletion_is_not_retried(self):
        with patch.object(cleanup.urllib.request, 'urlopen', side_effect=self.http_error(500)) as urlopen, \
                patch.object(cleanup.time, 'sleep') as sleep:
            with self.assertRaises(cleanup.ApiError):
                cleanup.Sonar('https://sonar.example', 'unused').delete_pr('project', '18')
        urlopen.assert_called_once()
        sleep.assert_not_called()

    def test_network_failure_is_retried(self):
        for error in (urllib.error.URLError('private-details'), TimeoutError()):
            with self.subTest(error=type(error).__name__), \
                    patch.object(cleanup.urllib.request, 'urlopen', side_effect=[error, self.response()]), \
                    patch.object(cleanup.time, 'sleep') as sleep, patch.object(cleanup, 'log'):
                self.assertEqual(cleanup.request('https://api.github.com', '/test', 'unused', retry=True),
                                 {'token': 'private-token'})
                sleep.assert_called_once_with(2)

    def test_retry_after_is_respected(self):
        with patch.object(cleanup.urllib.request, 'urlopen', side_effect=[
                self.http_error(503, {'Retry-After': '10'}), self.response()]), \
                patch.object(cleanup.time, 'sleep') as sleep, patch.object(cleanup, 'log'):
            cleanup.request('https://api.github.com', '/test', 'unused', retry=True)
        sleep.assert_called_once_with(10)

    def test_long_or_unrecognized_retry_after_does_not_retry_early(self):
        for value in ('60', 'Mon, 12 Oct 2026 13:00:00 GMT'):
            with self.subTest(value=value), \
                    patch.object(cleanup.urllib.request, 'urlopen', side_effect=self.http_error(503, {'Retry-After': value})), \
                    patch.object(cleanup.time, 'sleep') as sleep:
                with self.assertRaises(cleanup.ApiError):
                    cleanup.request('https://api.github.com', '/test', 'unused', retry=True)
                sleep.assert_not_called()

    def test_invalid_json_is_not_retried(self):
        with patch.object(cleanup.urllib.request, 'urlopen', return_value=self.response(b'not json')) as urlopen, \
                patch.object(cleanup.time, 'sleep') as sleep:
            with self.assertRaises(cleanup.ApiError):
                cleanup.request('https://api.github.com', '/test', 'unused', retry=True)
        urlopen.assert_called_once()
        sleep.assert_not_called()

    def test_error_log_has_explicit_platform_owner(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            cleanup.log(level='error', action='keep', error='HTTP 500')
        self.assertEqual(json.loads(output.getvalue())['alert_owner'], 'core-platform')


class SonarPullRequestCleanupTests(unittest.TestCase):
    def setUp(self):
        self.sonar = Mock()
        self.github = Mock(url='https://api.github.com')
        self.pr = {
            'state': 'closed', 'merged': True, 'merged_at': '2026-10-11T10:00:00Z',
            'closed_at': '2026-10-11T10:00:00Z', 'merge_commit_sha': 'a' * 40,
            'base': {'ref': 'release/next', 'repo': {'full_name': 'owner/repo'}},
        }
        self.analysis = {'key': 'analysis-id', 'date': '2026-10-11T14:01:00+0400', 'revision': 'b' * 40}
        self.gate = 'OK'
        self.tasks = {'queue': []}
        self.binding = {'alm': 'github', 'key': 'GitHub', 'url': 'https://api.github.com', 'repository': 'owner/repo'}
        self.sonar.get.side_effect = self.sonar_response
        self.github.get.side_effect = lambda repo, path, **query: (
            self.pr.copy() if path.startswith('pulls/') else {'status': 'ahead'})

    def sonar_response(self, path, **query):
        if path == '/api/project_pull_requests/list':
            return {'pullRequests': [{'key': '18'}]}
        if path == '/api/alm_settings/get_binding':
            return self.binding
        if path == '/api/project_analyses/search':
            self.assertEqual(query['branch'], 'release/next')
            return {'analyses': [self.analysis] if self.analysis else []}
        if path == '/api/qualitygates/project_status':
            self.assertEqual(query['analysisId'], 'analysis-id')
            return {'projectStatus': {'status': self.gate}}
        if path == '/api/ce/component':
            return self.tasks
        self.fail(f'Unexpected request: {path}')

    def run_cleanup(self, apply=True):
        return cleanup.cleanup_project(self.sonar, self.github, 'project', 'GitHub', apply)

    def assert_kept(self):
        result = self.run_cleanup()
        self.sonar.delete_pr.assert_not_called()
        self.assertEqual(result['kept'] + result['errors'], 1)

    def test_open_pr_is_kept_even_without_target_analysis(self):
        self.pr.update(state='open', merged=False, merged_at=None)
        self.analysis = None
        self.assert_kept()
        self.assertFalse(any(call.args[0] == '/api/project_analyses/search' for call in self.sonar.get.call_args_list))

    def test_closed_unmerged_pr_does_not_wait_for_target_analysis(self):
        self.pr.update(merged=False, merged_at=None, merge_commit_sha=None)
        self.analysis = None
        self.assertEqual(self.run_cleanup()['deleted'], 1)
        self.sonar.delete_pr.assert_called_once_with('project', '18')

    def test_merged_pr_requires_successful_analysis_containing_merge(self):
        for status in ('ahead', 'identical'):
            with self.subTest(status=status):
                self.sonar.delete_pr.reset_mock()
                self.github.get.side_effect = lambda repo, path, **query: (
                    self.pr.copy() if path.startswith('pulls/') else {'status': status})
                self.assertEqual(self.run_cleanup()['deleted'], 1)
                self.github.get.assert_any_call('owner/repo', f'compare/{"a" * 40}...{"b" * 40}', per_page=1)

    def test_failed_target_gate_keeps_merged_pr(self):
        self.gate = 'ERROR'
        self.assert_kept()

    def test_missing_target_analysis_keeps_merged_pr(self):
        self.analysis = None
        self.assert_kept()

    def test_old_target_analysis_keeps_merged_pr(self):
        self.analysis['date'] = '2026-10-11T13:59:59+0400'
        self.assert_kept()

    def test_missing_revision_keeps_merged_pr(self):
        self.analysis.pop('revision')
        self.assert_kept()

    def test_newer_analysis_of_unrelated_commit_is_not_sufficient(self):
        self.github.get.side_effect = lambda repo, path, **query: (
            self.pr.copy() if path.startswith('pulls/') else {'status': 'diverged'})
        self.assert_kept()

    def test_api_failure_does_not_delete_pr(self):
        self.github.get.side_effect = cleanup.ApiError('HTTP 403')
        self.assertEqual(self.run_cleanup()['errors'], 1)
        self.sonar.delete_pr.assert_not_called()

    def test_missing_merged_flag_does_not_classify_as_unmerged(self):
        self.pr.pop('merged')
        self.assertEqual(self.run_cleanup()['errors'], 1)
        self.sonar.delete_pr.assert_not_called()

    def test_reopened_pr_is_preserved(self):
        self.pr.update(merged=False, merged_at=None)
        self.github.get.side_effect = [self.pr.copy(), {**self.pr, 'state': 'open'}]
        self.assert_kept()

    def test_active_pr_analysis_is_preserved(self):
        for status in ('PENDING', 'IN_PROGRESS'):
            with self.subTest(status=status):
                self.tasks = {'queue': [{'pullRequest': '18', 'status': status}]}
                self.assert_kept()

    def test_dry_run_never_deletes(self):
        result = self.run_cleanup(apply=False)
        self.assertEqual(result['eligible'], 1)
        self.assertEqual(result['deleted'], 0)
        self.sonar.delete_pr.assert_not_called()

    def test_other_integration_is_not_touched(self):
        self.binding['key'] = 'Unrelated'
        self.assert_kept()
        self.github.get.assert_not_called()

    def test_project_listing_handles_pagination(self):
        sonar = cleanup.Sonar('https://sonar.example', 'unused')
        sonar.get = Mock(side_effect=[
            {'components': [{'key': str(i)} for i in range(100)], 'paging': {'total': 101}},
            {'components': [{'key': 'last'}], 'paging': {'total': 101}},
        ])
        self.assertEqual(len(list(sonar.projects())), 101)
        self.assertEqual(sonar.get.call_args.kwargs['p'], 2)


if __name__ == '__main__':
    unittest.main()
