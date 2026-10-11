"""Remove closed GitHub PR analyses; default to a read-only dry run."""

import argparse
import base64
import datetime
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


def log(**fields):
    print(json.dumps(fields, ensure_ascii=True), flush=True)


class ApiError(RuntimeError):
    pass


def request(base, path, authorization, *, method='GET', query=None, data=None, form=False):
    url = base.rstrip('/') + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    headers = {'Authorization': authorization, 'Accept': 'application/json',
               'User-Agent': 'panixida-sonarqube-pr-cleanup', 'X-GitHub-Api-Version': '2022-11-28'}
    body = None
    if data is not None:
        body = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, body, headers, method=method), timeout=20) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as error:
        # Never include response bodies, credentials or request headers in logs.
        raise ApiError(f'{method} {path}: HTTP {error.code}') from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ApiError(f'{method} {path}: request failed') from None


class Sonar:
    def __init__(self, url, password):
        self.url = url
        self.authorization = 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode()

    def get(self, path, **query):
        return request(self.url, path, self.authorization, query=query)

    def projects(self):
        page = 1
        while True:
            result = self.get('/api/components/search', qualifiers='TRK', p=page, ps=100)
            yield from result['components']
            if page * 100 >= result['paging']['total']:
                return
            page += 1

    def delete_pr(self, project, number):
        request(self.url, '/api/project_pull_requests/delete', self.authorization,
                method='POST', data={'project': project, 'pullRequest': number}, form=True)


class GitHub:
    def __init__(self, url, app_id, private_key):
        self.url = url
        self.app_id = app_id
        self.private_key = private_key
        self.tokens = {}

    def app_jwt(self):
        def encode(value):
            return base64.urlsafe_b64encode(value).rstrip(b'=')

        now = int(time.time())
        header = encode(b'{"alg":"RS256","typ":"JWT"}')
        payload = encode(json.dumps({'iat': now - 60, 'exp': now + 300, 'iss': self.app_id}).encode())
        unsigned = header + b'.' + payload
        signature = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', self.private_key],
                                   input=unsigned, capture_output=True, check=True).stdout
        return (unsigned + b'.' + encode(signature)).decode()

    def token(self, repository):
        if repository not in self.tokens:
            authorization = 'Bearer ' + self.app_jwt()
            installation = request(self.url, f'/repos/{repository}/installation', authorization)
            response = request(self.url, f'/app/installations/{installation["id"]}/access_tokens',
                               authorization, method='POST', data={
                                   'repositories': [repository.split('/')[1]],
                                   'permissions': {'contents': 'read', 'pull_requests': 'read'},
                               })
            self.tokens[repository] = response['token']
        return self.tokens[repository]

    def get(self, repository, suffix, **query):
        return request(self.url, f'/repos/{repository}/{suffix}',
                       'Bearer ' + self.token(repository), query=query)

    def close(self):
        for token in self.tokens.values():
            try:
                request(self.url, '/installation/token', 'Bearer ' + token, method='DELETE')
            except ApiError as error:
                log(level='warning', error=str(error))


def timestamp(value):
    result = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp has no timezone')
    return result


def pr_identity(pr):
    return (pr['state'], pr['merged'], pr.get('merged_at'), pr.get('merge_commit_sha'),
            pr['base']['ref'], pr['base']['repo']['full_name'].lower())


def deletion_reason(sonar, github, project, repository, pr):
    if pr['base']['repo']['full_name'].lower() != repository.lower():
        return None, 'repository mismatch'
    if pr['state'] == 'open':
        return None, 'open'
    if pr['state'] != 'closed':
        return None, 'unknown PR state'
    if pr['merged'] is False and pr.get('merged_at') is None and pr.get('closed_at'):
        return 'closed without merge', None
    if pr['merged'] is not True or not pr.get('merged_at'):
        return None, 'incomplete merge metadata'

    branch = pr['base']['ref']
    analyses = sonar.get('/api/project_analyses/search', project=project, branch=branch, ps=1)['analyses']
    if not analyses:
        return None, 'target branch has no completed analysis'
    analysis = analyses[0]
    revision = analysis.get('revision', '')
    merged_revision = pr.get('merge_commit_sha', '')
    if not all(isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (revision, merged_revision)):
        return None, 'missing commit revision'
    if timestamp(analysis['date']) < timestamp(pr['merged_at']):
        return None, 'target analysis predates merge'
    gate = sonar.get('/api/qualitygates/project_status', analysisId=analysis['key'])['projectStatus']['status']
    if gate != 'OK':
        return None, 'target Quality Gate is not OK'
    comparison = github.get(repository, f'compare/{merged_revision}...{revision}', per_page=1)
    if comparison['status'] not in ('identical', 'ahead'):
        return None, 'target analysis does not include merge commit'
    return f'merged and analyzed on {branch} at {revision}', None


def cleanup_project(sonar, github, project, integration_key, apply):
    counts = {'deleted': 0, 'eligible': 0, 'kept': 0, 'errors': 0}
    pull_requests = sonar.get('/api/project_pull_requests/list', project=project)['pullRequests']
    if not pull_requests:
        return counts
    binding = sonar.get('/api/alm_settings/get_binding', project=project)
    if (binding.get('alm') != 'github' or binding.get('key') != integration_key
            or binding.get('url', '').rstrip('/') != github.url.rstrip('/')):
        log(project=project, action='keep', reason='not bound to the configured GitHub integration')
        counts['kept'] += len(pull_requests)
        return counts
    repository = binding['repository']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository binding')
    for entry in pull_requests:
        number = entry['key']
        try:
            if not re.fullmatch(r'[1-9][0-9]*', number):
                raise ValueError('Invalid PR number')
            pr = github.get(repository, f'pulls/{number}')
            reason, keep = deletion_reason(sonar, github, project, repository, pr)
            if reason:
                tasks = sonar.get('/api/ce/component', component=project)
                active = tasks.get('queue', []) + [tasks.get('current', {})]
                if any(str(task.get('pullRequest')) == number
                       and task.get('status') in ('PENDING', 'IN_PROGRESS') for task in active):
                    reason, keep = None, 'PR analysis is running'
                elif pr_identity(github.get(repository, f'pulls/{number}')) != pr_identity(pr):
                    reason, keep = None, 'PR state changed during cleanup'
            if reason:
                counts['eligible'] += 1
                if apply:
                    sonar.delete_pr(project, number)
                    counts['deleted'] += 1
                log(project=project, pr=number, action='delete' if apply else 'would_delete', reason=reason)
            else:
                counts['kept'] += 1
                log(project=project, pr=number, action='keep', reason=keep)
        except (ApiError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
            counts['errors'] += 1
            log(level='error', project=project, pr=number, action='keep', error=str(error))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Delete eligible PR analyses (default: dry run)')
    parser.add_argument('--project', help='Limit execution to one SonarQube project key')
    args = parser.parse_args()
    credentials = pathlib.Path(os.environ.get('CREDENTIALS_DIRECTORY', '/var/run/sonarqube-cleanup'))
    sonar = Sonar(os.environ['SONAR_URL'], (credentials / 'password').read_text().rstrip('\r\n'))
    github = GitHub(os.environ.get('GITHUB_API_URL', 'https://api.github.com'),
                    (credentials / 'app-id').read_text().strip(), str(credentials / 'private-key'))
    integration_key = os.environ.get('GITHUB_INTEGRATION_KEY', 'GitHub')
    counts = {'projects': 0, 'deleted': 0, 'eligible': 0, 'kept': 0, 'errors': 0}
    try:
        for project in sonar.projects():
            key = project['key']
            if args.project and key != args.project:
                continue
            counts['projects'] += 1
            try:
                result = cleanup_project(sonar, github, key, integration_key, args.apply)
                for name, value in result.items():
                    counts[name] += value
            except (ApiError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
                counts['errors'] += 1
                log(level='error', project=key, action='keep', error=str(error))
        if args.project and counts['projects'] != 1:
            raise ValueError('Requested SonarQube project was not found')
    finally:
        github.close()
    log(action='summary', apply=args.apply, **counts)
    return 1 if counts['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
