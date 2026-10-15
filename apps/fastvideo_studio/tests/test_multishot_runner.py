# SPDX-License-Identifier: Apache-2.0
"""Dependencies must be scheduled by the process-isolated Studio owner."""
import threading
import time

import pytest

from fastvideo_studio.database import Database
from fastvideo_studio.job_runner import JobRunner, JobStatus


@pytest.fixture
def runner(tmp_path):
    r = JobRunner(str(tmp_path/'out'), str(tmp_path/'logs'), Database(tmp_path/'db.sqlite'))
    yield r
    r._shutdown()


def finished(*jobs):
    deadline = time.monotonic()+8
    while any(j.status in (JobStatus.QUEUED, JobStatus.RUNNING) for j in jobs):
        assert time.monotonic()<deadline
        time.sleep(.01)


def test_agent_http_uses_real_runner_and_persists_completed_take(runner, monkeypatch):
    """Exercise real request normalization/persistence/queue, replacing only GPU execution."""
    from fastapi.testclient import TestClient
    from fastvideo_studio import server
    from fastvideo_studio.agent_project import V2

    executed = []
    persisted = threading.Event()
    def run(job):
        executed.append(job.id)
        job.status = JobStatus.COMPLETED
        runner._save_job(job)
        persisted.set()

    monkeypatch.setattr(runner, '_run_job', run)
    monkeypatch.setattr(server, 'job_runner', runner, raising=False)
    monkeypatch.setattr(server, 'database', runner._db, raising=False)
    monkeypatch.setattr(server, '_agent_service', None)
    monkeypatch.setattr(server, '_available_models', [{'id': V2}])
    api = TestClient(server.app)
    response = api.post('/api/agent/projects', json={
        'title': 'Real runner contract',
        'shots': [{'id': 'wide', 'title': 'Arrival', 'prompt': '  A lighthouse at dawn  '}],
    })
    assert response.status_code == 201
    project = response.json()
    prefix = '/api/agent/projects/' + project['id']
    assert api.post(prefix + '/approve', json={'revision': 1}).status_code == 200
    response = api.post(prefix + '/generate', json={'revision': 1, 'max_new_jobs': 1})
    assert response.status_code == 200, response.text
    job_id = response.json()['selected']['wide']
    assert persisted.wait(8)
    finished(runner.get_job(job_id))
    assert executed == [job_id]
    assert runner._db.get_job(job_id)['status'] == 'completed'
    # Recreate the application service from SQLite; no new take may be submitted.
    monkeypatch.setattr(server, '_agent_service', None)
    assert api.post(prefix + '/generate', json={'revision': 1, 'max_new_jobs': 0}).status_code == 200
    assert executed == [job_id]
    assert api.post(prefix + '/review', json={
        'shot_id': 'wide', 'job_id': job_id, 'verdict': 'accepted',
    }).status_code == 200
    assert api.get(prefix + '/export-jobs').json()['job_ids'] == [job_id]


def test_reverse_submission_waits_for_predecessor(runner, monkeypatch):
    order=[]
    def run(job):
        order.append(job.id)
        job.status=JobStatus.COMPLETED
        runner._save_job(job)
    monkeypatch.setattr(runner,'_run_job',run)
    a=runner.create_job('a','m','first')
    b=runner.create_job('b','m','second',references=[{'source':'job-last-frame:a','media_type':'image'}])
    runner.enqueue_jobs(['b','a'])
    finished(a,b)
    assert order==['a','b']


def test_failed_predecessor_never_executes_dependents(runner,monkeypatch):
    order=[]
    def run(job):
        order.append(job.id)
        job.status=JobStatus.FAILED
        job.error='injected failure'
        runner._save_job(job)
    monkeypatch.setattr(runner,'_run_job',run)
    a=runner.create_job('a','m','first')
    b=runner.create_job('b','m','second',references=[{'source':'job-last-frame:a','media_type':'image'}])
    c=runner.create_job('c','m','third',references=[{'source':'job-last-frame:b','media_type':'image'}])
    runner.enqueue_jobs(['c','b','a'])
    finished(a,b,c)
    assert order==['a']
    assert b.status==c.status==JobStatus.FAILED
    assert 'failed' in b.error


def test_circular_batch_is_rejected_without_partial_enqueue(runner):
    a=runner.create_job('a','m','first',references=[{'source':'job-last-frame:b','media_type':'image'}])
    b=runner.create_job('b','m','second',references=[{'source':'job-last-frame:a','media_type':'image'}])
    with pytest.raises(ValueError,match='loop|circular'):
        runner.enqueue_jobs(['a','b'])
    assert a.status==b.status==JobStatus.PENDING


def test_dequeue_waiting_job_preserves_pending(runner,monkeypatch):
    a=runner.create_job('a','m','first')
    b=runner.create_job('b','m','second',references=[{'source':'job-last-frame:a','media_type':'image'}])
    runner.enqueue_job('b')
    runner.dequeue_job('b')
    assert b.status==JobStatus.PENDING
    runner._load_jobs()
    assert runner.get_job('b').status==JobStatus.PENDING


@pytest.mark.parametrize('path,body,status', [
    ('/api/jobs/queue', {'job_ids': ['missing']}, 404),
    ('/api/jobs/missing/queue', None, 404),
    ('/api/jobs/a/dequeue', None, 409),
    ('/api/jobs/queue', {'job_ids': ['a', 'a']}, 409),
])
def test_queue_api_rejects_invalid_requests(runner, monkeypatch, path, body, status):
    from fastapi.testclient import TestClient
    from fastvideo_studio import server
    runner.create_job('a', 'm', 'first')
    monkeypatch.setattr(server, 'job_runner', runner, raising=False)
    response = TestClient(server.app, raise_server_exceptions=False).post(path, json=body)
    assert response.status_code == status
    assert runner.get_job('a').status == JobStatus.PENDING


def test_transitive_running_descendant_blocks_source_edit(runner):
    a = runner.create_job('a', 'm', 'first')
    b = runner.create_job('b', 'm', 'second', references=[{'source': 'job-last-frame:a', 'media_type': 'image'}])
    c = runner.create_job('c', 'm', 'third', references=[{'source': 'job-last-frame:b', 'media_type': 'image'}])
    a.status = b.status = JobStatus.COMPLETED
    c.status = JobStatus.RUNNING
    with pytest.raises(ValueError, match='dependent'):
        runner._media_job('a')
    c.status = JobStatus.PENDING


def test_enqueue_cannot_race_source_edit(runner, monkeypatch):
    from fastvideo_studio import job_runner as module
    a = runner.create_job('a', 'm', 'first')
    a.status = JobStatus.COMPLETED
    runner.create_job('b', 'm', 'second', references=[{'source': 'job-last-frame:a', 'media_type': 'image'}])
    checked = []
    def trim(*args):
        with pytest.raises(ValueError, match='edit'):
            runner.enqueue_job('b')
        checked.append(True)
        return '/tmp/new.mp4', 24
    monkeypatch.setattr(module, 'trim_video', trim)
    runner.trim_job('a')
    assert checked
    assert runner.get_job('b').status == JobStatus.PENDING


def test_queue_persistence_failure_does_not_mutate_or_dispatch(runner, monkeypatch):
    import sqlite3
    a = runner.create_job('a', 'm', 'first')
    b = runner.create_job('b', 'm', 'second')
    def fail(*args):
        raise sqlite3.OperationalError('injected disk failure')
    monkeypatch.setattr(runner._db, 'queue_jobs', fail, raising=False)
    with pytest.raises(sqlite3.OperationalError):
        runner.enqueue_jobs(['a', 'b'])
    assert a.status == b.status == JobStatus.PENDING
    assert all(row['status'] == 'pending' for row in runner._db.get_all_jobs())


def test_new_attempt_resets_previous_edit_metadata(runner):
    runner.create_job('source', 'm', 'source')
    job = runner.create_job('a', 'm', 'clip', references=[{'source': 'job-last-frame:source', 'media_type': 'image'}])
    job.status = JobStatus.FAILED
    job.edit_start_seconds = .5
    job.edit_end_seconds = 1.5
    job.edit_segments = [{'brightness': 25}]
    runner._db.update_job_media('a', '/tmp/old.mp4', 24, .5, 1.5, job.edit_segments)
    runner.enqueue_job('a')
    assert (job.edit_start_seconds, job.edit_end_seconds, job.edit_segments) == (0, None, [])
    row = next(row for row in runner._db.get_all_jobs() if row['id'] == 'a')
    assert row['edit_start_seconds'] == 0
    assert row['edit_end_seconds'] is None
    assert row['edit_segments_json'] == '[]'


def test_retry_and_duplicate_use_original_generation_length(runner):
    job = runner.create_job('a', 'm', 'clip', num_frames=124)
    job.status = JobStatus.COMPLETED
    runner._set_output(job, '/tmp/trimmed.mp4', 24, 0, 1)
    copy = runner.duplicate_job('a', 'copy')
    assert copy.num_frames == 124
    job.status = JobStatus.FAILED
    runner._save_job(job)
    runner._load_jobs()
    job = runner.get_job('a')
    with runner._condition:
        runner.enqueue_job('a')
        assert job.num_frames == 124
        runner.dequeue_job('a')
    row = next(row for row in runner._db.get_all_jobs() if row['id'] == 'a')
    assert row['num_frames'] == 124


def test_source_publication_and_invalidation_rollback_together(runner):
    import sqlite3
    a = runner.create_job('a', 'm', 'first')
    b = runner.create_job('b', 'm', 'second', references=[{'source': 'job-last-frame:a', 'media_type': 'image'}])
    a.status = b.status = JobStatus.COMPLETED
    a.output_path = '/tmp/original.mp4'
    runner._save_job(a)
    runner._save_job(b)
    with runner._db._conn() as conn:
        conn.execute("CREATE TRIGGER fail_invalidation BEFORE UPDATE OF status ON jobs WHEN OLD.id='b' "
                     "BEGIN SELECT RAISE(ABORT, 'injected invalidation failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        runner._set_output(a, '/tmp/new.mp4', 24)
    assert a.output_path == '/tmp/original.mp4'
    assert b.status == JobStatus.COMPLETED
    rows = {row['id']: row for row in runner._db.get_all_jobs()}
    assert rows['a']['output_path'] == '/tmp/original.mp4'
    assert rows['b']['status'] == 'completed'


def test_fasth3_v2_reference_job_is_rejected_before_queueing(runner):
    runner.create_job('source', 'm', 'first')
    job = runner.create_job('v2', 'FastVideo/FastVideo-FastH3-8-Step-V2', 'clip',
                            references=[{'source': 'job-last-frame:source', 'media_type': 'image'}])
    with pytest.raises(ValueError, match='T2AV|reference'):
        runner.enqueue_job(job.id)
    assert job.status == JobStatus.PENDING


def test_queue_checks_approved_payload_atomically(runner):
    job=runner.create_job('approved','m','unapproved text')
    with pytest.raises(ValueError,match='approved'):
        runner.enqueue_jobs(['approved'],expected_jobs={'approved':{'prompt':'approved text'}})
    assert job.status==JobStatus.PENDING
    assert runner._db.get_job(job.id)['status']=='pending'
