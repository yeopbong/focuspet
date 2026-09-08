"""Integration tests use isolated synthetic/test stores, never native input hooks."""
import time

from focuspet.service import AppService


def wait_for(service, predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = service.snapshot()
        if predicate(value):
            return value
        time.sleep(.05)
    raise AssertionError(service.snapshot())


def test_demo_first_window_feedback_revision_pause_and_delete(tmp_path):
    service = AppService(mode='synthetic-demo', data_dir=tmp_path)
    try:
        assert service.wait_ready()
        first = wait_for(service, lambda s: len(s['history']) >= 3)
        assert first['state'] in ('Focused', 'Normal', 'Distracted', 'Unknown')
        assert first['mode'] == 'synthetic-demo'
        service.command('feedback', label='Normal', minutes=1)
        feedback = wait_for(service, lambda s: len(s['feedback']) == 1)['feedback'][0]
        assert feedback['label'] == 'Normal'
        service.command('feedback', label='Focused', start=feedback['start'], end=feedback['end'], revision_of=feedback['id'])
        amended = wait_for(service, lambda s: len(s['feedback']) == 2)
        assert amended['feedback'][0]['revises'] == feedback['id']
        service.command('retract_feedback', id=amended['feedback'][0]['id'])
        withdrawn = wait_for(service, lambda s: all(f['withdrawn'] for f in s['feedback']))
        assert withdrawn['learning']['episodes'] == 0
        service.command('rest', minutes=5)
        rest = wait_for(service, lambda s: s['state'] == 'Rest')
        assert rest['focus'] is None
        service.command('pause')
        paused = wait_for(service, lambda s: s['observed_state'] == 'Paused')
        before = paused['workload']
        time.sleep(.5)
        assert service.snapshot()['workload'] == before
        service.command('delete_data', confirm=True)
        deleted = wait_for(service, lambda s: not s['history'] and not s['feedback'])
        assert deleted['focus'] is None
        assert deleted['workload'] == 0
        assert deleted['consent'] is False
        assert not (tmp_path / 'real').exists()
    finally:
        service.close()
    assert not service._thread.is_alive()


def test_test_mode_never_starts_native_and_cancel_insufficient_job(tmp_path):
    service = AppService(mode='test', data_dir=tmp_path)
    try:
        assert service.wait_ready()
        initial = service.snapshot()
        assert initial['consent'] is False
        assert all(v == 'unavailable' for v in initial['permissions'].values())
        service.command('train')
        result = wait_for(service, lambda s: s.get('last_job', {}).get('status') == 'insufficient-evidence', seconds=15)
        assert result['last_job']['episodes'] == 0
        assert not result.get('job_running')
        assert result['learning']['version'] == 'generic-prior-v1'
        service.command('calibrate')
        insufficient = wait_for(service, lambda s: s.get('last_job', {}).get('reports') == 0, seconds=15)
        assert insufficient['last_job']['status'] == 'insufficient-evidence'
    finally:
        service.close()
