from scripts.candidate_gate import candidate_run
import io
import json
import urllib.error
from unittest.mock import patch
import pytest
from scripts.candidate_gate import read_runs, wait


def test_gate_rejects_different_head_branch_event_or_workflow():
    run = dict(head_sha='a'*40, head_branch='candidate/a', event='push',
               path='.github/workflows/validate.yml', run_number=1, run_attempt=1)
    assert candidate_run([run], 'a'*40, 'candidate/a') == run
    for field in ('head_sha', 'head_branch', 'event', 'path'):
        assert candidate_run([{**run, field: 'other'}], 'a'*40, 'candidate/a') is None
    newer = {**run, 'run_attempt': 2, 'conclusion': 'failure'}
    assert candidate_run([run, newer], 'a'*40, 'candidate/a') == newer


def test_transport_retry_is_bounded_and_permission_errors_are_not_retried():
    with patch('scripts.candidate_gate.urllib.request.urlopen', side_effect=[
            urllib.error.URLError('interrupted'), io.BytesIO(b'{"workflow_runs": []}')]) as open_, \
            patch('scripts.candidate_gate.time.sleep'):
        assert read_runs('https://example.test') == []
        assert open_.call_count == 2
    for error, expected in ((urllib.error.URLError('interrupted'), 3),
                            (urllib.error.HTTPError('url', 403, 'denied', {}, None), 1)):
        with patch('scripts.candidate_gate.urllib.request.urlopen', side_effect=error) as open_, \
                patch('scripts.candidate_gate.time.sleep'):
            with pytest.raises(urllib.error.URLError):
                read_runs('https://example.test')
            assert open_.call_count == expected


def test_only_explicit_success_can_release_a_matching_candidate(capsys):
    run = dict(head_sha='a'*40, head_branch='candidate/a', event='push',
               path='.github/workflows/validate.yml', run_number=1, run_attempt=1,
               status='completed', id=1, html_url='https://example.test/run')
    for conclusion in ('failure', 'cancelled', 'neutral', 'skipped', 'timed_out'):
        with patch('scripts.candidate_gate.read_runs', return_value=[{**run, 'conclusion': conclusion}]):
            with pytest.raises(RuntimeError):
                wait('a'*40, 'candidate/a')
    with patch('scripts.candidate_gate.read_runs', return_value=[{**run, 'conclusion': 'success'}]):
        wait('a'*40, 'candidate/a')
    assert json.loads(capsys.readouterr().out)['status'] == 'succeeded'
