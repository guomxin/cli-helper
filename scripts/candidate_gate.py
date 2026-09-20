"""Verify GitHub Actions for this immutable candidate before deployment."""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def candidate_run(runs, commit, branch):
    matches = [run for run in runs if run.get('head_sha') == commit
               and run.get('head_branch') == branch and run.get('event') == 'push'
               and run.get('path') == '.github/workflows/validate.yml']
    return max(matches, key=lambda run: (run['run_number'], run.get('run_attempt', 1)), default=None)


def read_runs(request):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)['workflow_runs']
        except urllib.error.HTTPError:
            raise  # Permission, rate-limit and HTTP failures require inspection.
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def wait(commit, branch, timeout=2400):
    url = 'https://api.github.com/repos/guomxin/cli-helper/actions/runs?' + urllib.parse.urlencode(
        {'head_sha': commit, 'branch': branch, 'event': 'push', 'per_page': 100})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                                     'User-Agent': 'AgentBridge-candidate-gate'})
        # A bounded transport retry is never evidence of a pass; TLS validation stays on.
        run = candidate_run(read_runs(request), commit, branch)
        if run and run['status'] == 'completed':
            if run['conclusion'] != 'success':
                raise RuntimeError(f"Candidate CI did not pass: {run['conclusion']} {run['html_url']}")
            print(json.dumps({'commit': commit, 'branch': branch, 'runId': run['id'],
                              'url': run['html_url'], 'status': 'succeeded'}))
            return
        time.sleep(45)
    raise TimeoutError('Candidate CI not proven before deadline; deployment blocked')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--branch', required=True)
    args = parser.parse_args()
    wait(args.commit, args.branch)
