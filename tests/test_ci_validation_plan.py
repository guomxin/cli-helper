"""Prove that scope reduction cannot hide code changes or stale CI failures."""
import subprocess
from unittest.mock import patch

import pytest

from scripts.ci_validation_plan import classify, latest_trusted, plan

SHA = 'a' * 40
BASE = 'b' * 40
REPO = 'guomxin/cli-helper'


def run(sha=BASE, **overrides):
    return dict(dict(id=10, head_sha=sha, head_branch=f'candidate/{sha}', event='push',
                     path='.github/workflows/validate.yml', head_repository={'full_name': REPO},
                     run_number=10, run_attempt=1, status='completed', conclusion='success',
                     html_url='https://example.test/run/10'), **overrides)


def environment(**overrides):
    return dict(dict(GITHUB_SHA=SHA, GITHUB_EVENT_NAME='push', GITHUB_REPOSITORY=REPO,
                     GITHUB_RUN_ID='20', GITHUB_REF=f'refs/heads/candidate/{SHA}'), **overrides)


@pytest.mark.parametrize('paths,expected', [
    (['docs/a.md', 'README.md'], 'docs'),
    (['docs/a.md', 'deploy/release-policy.json'], 'release'),
    (['deploy/systemd/agentbridge.service'], 'full'),
    (['docs/a.md', 'bscli/core/tasks.py'], 'full'),
    (['docs/example.py'], 'full'),
    (['.github/workflows/validate.yml'], 'full'),
    (['scripts/ci_validation_plan.py'], 'full'),
    (['tests/test_ci_validation_plan.py'], 'full'),
    (['pyproject.toml'], 'full'),
    (['unknown.file'], 'full'), ([], 'full'),
])
def test_scope_is_an_allowlist(paths, expected):
    assert classify(paths) == expected


@pytest.mark.parametrize('override', [
    {'head_sha': SHA}, {'head_branch': 'feature'}, {'event': 'pull_request'},
    {'path': '.github/workflows/other.yml'}, {'head_repository': {'full_name': 'fork/repo'}},
    {'status': 'in_progress'}, {'conclusion': 'skipped'}, {'conclusion': 'failure'},
])
def test_reuse_rejects_untrusted_or_incomplete_evidence(override):
    assert latest_trusted([run(**override)], BASE, REPO) is None


def test_newer_failed_attempt_blocks_old_green():
    assert latest_trusted([run(), run(run_attempt=2, conclusion='failure')], BASE, REPO) is None
    assert latest_trusted([run(), run(id=11, run_number=11, status='in_progress')], BASE, REPO) is None


def test_main_reuses_only_identical_candidate_and_manual_rerun_does_not(tmp_path):
    fetch = lambda *_: [run(SHA)]
    env = environment(GITHUB_REF='refs/heads/main')
    assert plan(tmp_path, env, {}, fetch)['profile'] == 'reuse'
    assert plan(tmp_path, {**env, 'GITHUB_RUN_ATTEMPT': '2'}, {}, fetch)['profile'] == 'full'
    assert plan(tmp_path, {**env, 'GITHUB_EVENT_NAME': 'workflow_dispatch'}, {}, fetch)['profile'] == 'full'
    with patch('scripts.ci_validation_plan.git', return_value=BASE):
        assert plan(tmp_path, environment(), {}, fetch)['profile'] == 'full'


def test_entire_baseline_diff_includes_earlier_code_commit(tmp_path):
    with patch('scripts.ci_validation_plan.git', side_effect=[BASE, BASE, 'docs/latest.md\0bscli/core/tasks.py\0']) as git:
        result = plan(tmp_path, environment(), {}, lambda *_: [run()])
    assert result['profile'] == 'full'
    assert git.call_args.args[1:] == ('diff', '--no-renames', '--name-only', '-z', BASE, SHA)


def test_scoped_check_requires_verified_ancestor(tmp_path):
    for files, expected in [('docs/latest.md\0', 'docs'), ('deploy/release-policy.json\0', 'release')]:
        with patch('scripts.ci_validation_plan.git', side_effect=[BASE, BASE, files]):
            result = plan(tmp_path, environment(), {}, lambda *_: [run()])
        assert result['profile'] == expected
        assert result['baseline'] == BASE
    with patch('scripts.ci_validation_plan.git', side_effect=[BASE, SHA]):
        assert plan(tmp_path, environment(), {}, lambda *_: [run()])['profile'] == 'full'
    with patch('scripts.ci_validation_plan.git', side_effect=[BASE, BASE]):
        assert plan(tmp_path, environment(), {}, lambda *_: [])['profile'] == 'full'


def test_api_and_missing_history_fail_to_full(tmp_path):
    for error in [OSError('API unavailable'), subprocess.CalledProcessError(1, 'git')]:
        with patch('scripts.ci_validation_plan.git', side_effect=error):
            assert plan(tmp_path, environment(), {}, lambda *_: [run()])['profile'] == 'full'
    with patch('scripts.ci_validation_plan.github_runs', side_effect=OSError('API unavailable')) as fetch:
        assert plan(tmp_path, environment(GITHUB_REF='refs/heads/main'), {}, fetch)['profile'] == 'full'


def test_pull_request_uses_merge_base_not_latest_commit(tmp_path):
    with patch('scripts.ci_validation_plan.git', side_effect=[BASE, BASE, 'README.md\0']) as git:
        result = plan(tmp_path, environment(GITHUB_EVENT_NAME='pull_request'),
                      {'pull_request': {'base': {'sha': BASE}}}, lambda *_: [run()])
    assert result['profile'] == 'docs'
    assert git.call_args_list[0].args[1:] == ('merge-base', SHA, BASE)


def test_real_history_detects_code_before_document_and_rename(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init', '-q')
    git('config', 'user.email', 'test@example.test')
    git('config', 'user.name', 'CI test')
    (tmp_path / 'runtime.py').write_text('value = 1\n')
    git('add', '.')
    git('commit', '-qm', 'baseline')
    base = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/remotes/origin/main', base)
    (tmp_path / 'runtime.py').write_text('value = 2\n')
    git('commit', '-qam', 'runtime change')
    (tmp_path / 'README.md').write_text('docs\n')
    git('add', '.')
    git('commit', '-qm', 'later docs')
    sha = git('rev-parse', 'HEAD')
    result = plan(tmp_path, environment(GITHUB_SHA=sha), {}, lambda *_: [run(base)])
    assert result['profile'] == 'full'
    assert set(result['changedPaths']) == {'runtime.py', 'README.md'}
    git('update-ref', 'refs/remotes/origin/main', sha)
    git('mv', 'runtime.py', 'renamed.md')
    git('commit', '-qm', 'rename code')
    result = plan(tmp_path, environment(GITHUB_SHA=git('rev-parse', 'HEAD')), {}, lambda *_: [run(sha)])
    assert result['profile'] == 'full'
    assert set(result['changedPaths']) == {'runtime.py', 'renamed.md'}
