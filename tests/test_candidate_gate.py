from scripts.candidate_gate import candidate_run


def test_gate_rejects_different_head_branch_event_or_workflow():
    run = dict(head_sha='a'*40, head_branch='candidate/a', event='push',
               path='.github/workflows/validate.yml', run_number=1, run_attempt=1)
    assert candidate_run([run], 'a'*40, 'candidate/a') == run
    for field in ('head_sha', 'head_branch', 'event', 'path'):
        assert candidate_run([{**run, field: 'other'}], 'a'*40, 'candidate/a') is None
    newer = {**run, 'run_attempt': 2, 'conclusion': 'failure'}
    assert candidate_run([run, newer], 'a'*40, 'candidate/a') == newer
