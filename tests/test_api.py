from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from doctor import main
from doctor.db import Base, Incident, FixJob, AgentRun
import pytest
import asyncio
import httpx


@pytest.fixture
def controls(monkeypatch):
    from sqlalchemy import event
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'Session', factory)
    with factory.begin() as session:
        session.add(Incident(id=1, fingerprint='controls', diagnosis_status='DONE'))
    return TestClient(main.app, base_url='http://127.0.0.1'), factory, {'X-Doctor-Action': '1'}


def test_delete_confirmation_links_pending_work_and_id_nonreuse(controls):
    from doctor.db import Diagnosis, Deployment, Occurrence, IncidentLogWindow, State, next_id
    client, factory, headers = controls
    with factory.begin() as session:
        session.add(FixJob(id=12, incident_id=1, status='QUEUED'))
        session.flush()
        session.add(Deployment(fix_job_id=12, status='QUEUED'))
        session.add(Diagnosis(incident_id=1))
        session.add(Occurrence(incident_id=1, source_id='occ'))
        session.add(AgentRun(incident_id=1))
        session.add(IncidentLogWindow(incident_id=1, anchor_id=1, container_id='test'))
        session.add(State(key='chat:1', value={'messages': []}))
        session.add(State(key='cooldown:1', value={'until': 0}))
        session.add(State(key='diagnosis-model:1', value={'model': 'test'}))
    assert client.request('DELETE', '/incidents/1', headers=headers, json={'confirmed': False}).status_code == 403
    assert client.request('DELETE', '/incidents/1', headers=headers, json={'confirmed': True}).status_code == 200
    with factory.begin() as session:
        for model in (Incident, FixJob, Diagnosis, Deployment, Occurrence, AgentRun, IncidentLogWindow):
            assert session.query(model).count() == 0
        assert session.get(State, 'chat:1') is None and session.get(State, 'cooldown:1') is None
        assert next_id(session, FixJob) > 12 and next_id(session, Incident) > 1
    assert client.get('/incidents/1').status_code == 404


@pytest.mark.parametrize('active', ['diagnosis', 'fix', 'deployment', 'unknown-deployment', 'interrupted-deployment'])
def test_delete_and_status_changes_refuse_active_work(controls, active):
    from doctor.db import Deployment
    client, factory, headers = controls
    with factory.begin() as session:
        if active == 'diagnosis':
            session.get(Incident, 1).diagnosis_status = 'ANALYZING'
        else:
            session.add(FixJob(id=1, incident_id=1, status='RUNNING' if active == 'fix' else 'READY'))
            session.flush()
            if 'deployment' in active:
                status = 'UNKNOWN' if active.startswith('unknown') else 'INTERRUPTED' if active.startswith('interrupted') else 'DEPLOYING'
                session.add(Deployment(fix_job_id=1, status=status))
    assert client.request('DELETE', '/incidents/1', headers=headers, json={'confirmed': True}).status_code == 409
    assert client.patch('/incidents/1/status', headers=headers, json={'status': 'NO_ACTION'}).status_code == 409
    with factory() as session:
        assert session.get(Incident, 1)


def test_manual_status_no_action_cancels_queues_and_prevents_actions(controls):
    from doctor.db import Deployment, State
    client, factory, headers = controls
    with factory.begin() as session:
        session.add(FixJob(id=1, incident_id=1, status='QUEUED'))
        session.flush()
        session.add(Deployment(fix_job_id=1, status='QUEUED'))
        session.add(State(key='cooldown:1', value={'until': 0}))
    response = client.patch('/incidents/1/status', headers=headers, json={'status': 'NO_ACTION'})
    assert response.status_code == 200 and response.json()['diagnosis_status'] == 'SKIPPED'
    with factory() as session:
        assert session.get(FixJob, 1).status == 'ABORTED'
        assert session.query(Deployment).first().status == 'ABORTED'
        assert session.get(State, 'cooldown:1') is None
    assert client.post('/incidents/1/analyze', headers=headers, json={}).status_code == 409
    assert client.post('/incidents/1/fix', headers=headers, json={}).status_code == 409
    assert client.post('/fixes/1/continue', headers=headers).status_code == 409
    assert client.patch('/incidents/1/status', headers=headers, json={'status': 'UNKNOWN'}).status_code == 422
    assert client.patch('/incidents/1/status', headers=headers, json={'status': 'OPEN'}).status_code == 200


def test_manual_git_api_is_explicit_and_forwards_only_prepared_workspace(controls, monkeypatch):
    from doctor.db import State
    client, factory, headers = controls
    calls = []
    async def runner(operation, payload, timeout):
        with factory() as session:
            assert session.get(State, 'manual-git:1').value['status'] == 'RUNNING'
        calls.append((operation, payload))
        return {'branch': payload['branch']}
    monkeypatch.setattr(main, 'runner_call', runner)
    with factory.begin() as session:
        session.add(FixJob(id=1, incident_id=1, status='READY', patch_sha='a' * 64, workspace='job-1'))
    body = {'confirmed': True, 'patch_sha': 'a' * 64, 'action': 'branch', 'branch': 'doctor/test'}
    assert client.post('/fixes/1/git', json=body).status_code == 403
    assert client.post('/fixes/1/git', headers=headers, json={**body, 'confirmed': False}).status_code == 403
    assert client.post('/fixes/1/git', headers=headers, json={**body, 'patch_sha': 'b' * 64}).status_code == 409
    assert not calls
    assert client.post('/fixes/1/git', headers=headers, json=body).status_code == 200
    assert calls[0][0] == 'manual-git' and calls[0][1]['workspace_id'] == 'job-1'
    for action in ('commit', 'push'):
        assert client.post('/fixes/1/git', headers=headers, json={**body, 'action': action, 'message': 'fix'}).status_code == 200
    with factory() as session:
        assert session.get(State, 'manual-git:1').value['status'] == 'SUCCEEDED'
    assert client.patch('/incidents/1/status', headers=headers, json={'status': 'NO_ACTION'}).status_code == 200
    assert client.request('DELETE', '/incidents/1', headers=headers, json={'confirmed': True}).status_code == 200
    with factory() as session:
        assert session.get(State, 'manual-git:1') is None


def stub_control_restart(monkeypatch):
    async def idle_worker():
        await asyncio.Event().wait()
    monkeypatch.setattr(main, 'init_db', lambda: None)
    for name in ('diagnosis_worker', 'fix_worker', 'parser_worker', 'retention_worker'):
        monkeypatch.setattr(main, name, idle_worker)
    # A real restart discards process-local locks, but not committed SQLite State.
    monkeypatch.setattr(main, 'manual_git_lock', asyncio.Lock())


def test_restart_interrupted_deploy_still_blocks_controls_and_preserves_history(controls, monkeypatch):
    from doctor.db import Deployment
    _, factory, headers = controls
    with factory.begin() as session:
        session.add(FixJob(id=1, incident_id=1, status='READY'))
        session.flush()
        session.add(Deployment(id=1, fix_job_id=1, status='DEPLOYING', result={'evidence': 'preserve'}))
    stub_control_restart(monkeypatch)
    with TestClient(main.app, base_url='http://127.0.0.1') as restarted:
        assert restarted.request('DELETE', '/incidents/1', headers=headers, json={'confirmed': True}).status_code == 409
        for status in ('NO_ACTION', 'RESOLVED', 'OPEN'):
            assert restarted.patch('/incidents/1/status', headers=headers, json={'status': status}).status_code == 409
        history = restarted.get('/deployments').json()
        assert history[0]['status'] == 'INTERRUPTED' and history[0]['result'] == {'evidence': 'preserve'}
    with factory() as session:
        assert session.get(Deployment, 1) and session.get(FixJob, 1) and session.get(Incident, 1)


def test_git_timeout_and_restart_do_not_release_durable_guard(controls, monkeypatch):
    from doctor.db import State
    client, factory, headers = controls
    with factory.begin() as session:
        session.add(FixJob(id=1, incident_id=1, status='READY', patch_sha='a' * 64, workspace='job-1'))
    calls = []
    async def still_running(operation, payload, timeout):
        # The host may be waiting on its lock or pushing after this timeout.
        with factory() as session:
            value = session.get(State, 'manual-git:1').value
            assert value['status'] == 'RUNNING' and value['action'] == 'push'
        calls.append(payload)
        raise httpx.ReadTimeout('Sensitive provider detail must not be persisted')
    monkeypatch.setattr(main, 'runner_call', still_running)
    body = {'confirmed': True, 'patch_sha': 'a' * 64, 'action': 'push', 'branch': 'doctor/test'}
    response = client.post('/fixes/1/git', headers=headers, json=body)
    assert response.status_code == 409 and 'Sensitive' not in response.text
    assert not main.manual_git_lock.locked()
    with factory() as session:
        value = session.get(State, 'manual-git:1').value
        assert value['status'] == 'UNKNOWN' and 'Sensitive' not in repr(value)
    stub_control_restart(monkeypatch)
    with TestClient(main.app, base_url='http://127.0.0.1') as restarted:
        assert restarted.request('DELETE', '/incidents/1', headers=headers, json={'confirmed': True}).status_code == 409
        assert restarted.patch('/incidents/1/status', headers=headers, json={'status': 'NO_ACTION'}).status_code == 409
        assert restarted.post('/fixes/1/git', headers=headers, json=body).status_code == 409
        assert len(calls) == 1


def test_confirmed_runner_git_error_allows_retry_and_incident_controls(controls, monkeypatch):
    from doctor.db import State
    client, factory, headers = controls
    with factory.begin() as session:
        session.add(FixJob(id=1, incident_id=1, status='READY', patch_sha='a' * 64, workspace='job-1'))
    async def rejected(*args, **kwargs):
        request = httpx.Request('POST', 'http://runner/manual-git')
        response = httpx.Response(409, request=request, text='Sensitive host detail')
        raise httpx.HTTPStatusError('Sensitive host detail', request=request, response=response)
    monkeypatch.setattr(main, 'runner_call', rejected)
    body = {'confirmed': True, 'patch_sha': 'a' * 64, 'action': 'branch', 'branch': 'doctor/test'}
    result = client.post('/fixes/1/git', headers=headers, json=body)
    assert result.status_code == 409 and 'Sensitive' not in result.text
    with factory() as session:
        assert session.get(State, 'manual-git:1').value['status'] == 'FAILED'
    assert client.patch('/incidents/1/status', headers=headers, json={'status': 'OPEN'}).status_code == 200
    async def completed(*args, **kwargs):
        return {'branch': 'doctor/test'}
    monkeypatch.setattr(main, 'runner_call', completed)
    assert client.post('/fixes/1/git', headers=headers, json=body).status_code == 200
    assert client.request('DELETE', '/incidents/1', headers=headers, json={'confirmed': True}).status_code == 200


def test_restart_preserves_unacknowledged_running_git_admission(controls, monkeypatch):
    from doctor.db import State
    _, factory, headers = controls
    with factory.begin() as session:
        # Simulate process death after durable admission, before any response.
        session.add(State(key='manual-git:1', value={'status': 'RUNNING', 'operation_id': 'old-request', 'action': 'push'}))
    stub_control_restart(monkeypatch)
    with TestClient(main.app, base_url='http://127.0.0.1') as restarted:
        with factory() as session:
            assert session.get(State, 'manual-git:1').value['status'] == 'UNKNOWN'
        assert restarted.request('DELETE', '/incidents/1', headers=headers, json={'confirmed': True}).status_code == 409
        assert restarted.patch('/incidents/1/status', headers=headers, json={'status': 'OPEN'}).status_code == 409
    # An unrelated/late completion cannot clear a different operation's guard.
    main.finish_manual_git('manual-git:1', 'different-request', 'SUCCEEDED')
    with factory() as session:
        assert session.get(State, 'manual-git:1').value['status'] == 'UNKNOWN'


@pytest.mark.asyncio
async def test_cancelled_git_request_keeps_unknown_guard_after_lock_released(controls, monkeypatch):
    from doctor.db import State
    from fastapi import HTTPException
    _, factory, _ = controls
    with factory.begin() as session:
        session.add(FixJob(id=1, incident_id=1, status='READY', patch_sha='a' * 64, workspace='job-1'))
    async def disconnected(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(main, 'runner_call', disconnected)
    with pytest.raises(asyncio.CancelledError):
        await main.manual_git(1, main.ManualGitRequest(confirmed=True, patch_sha='a' * 64, action='push', branch='doctor/test'))
    assert not main.manual_git_lock.locked()
    with factory() as session:
        assert session.get(State, 'manual-git:1').value['status'] == 'UNKNOWN'
    with pytest.raises(HTTPException) as error:
        await main.delete_incident(1, main.ConfirmationRequest(confirmed=True))
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_other_api_instance_cannot_remove_incident_while_host_git_waits(controls, monkeypatch):
    from doctor.db import State
    from fastapi import HTTPException
    _, factory, _ = controls
    with factory.begin() as session:
        session.add(FixJob(id=1, incident_id=1, status='READY', patch_sha='a' * 64, workspace='job-1'))
    accepted, release = asyncio.Event(), asyncio.Event()
    async def waiting_host(*args, **kwargs):
        accepted.set()
        await release.wait()
        return {'branch': 'doctor/test'}
    monkeypatch.setattr(main, 'runner_call', waiting_host)
    request = asyncio.create_task(main.manual_git(1, main.ManualGitRequest(
        confirmed=True, patch_sha='a' * 64, action='branch', branch='doctor/test')))
    try:
        await accepted.wait()
        # A second API instance/restart has a fresh process-local lock.
        monkeypatch.setattr(main, 'manual_git_lock', asyncio.Lock())
        with pytest.raises(HTTPException) as error:
            await main.delete_incident(1, main.ConfirmationRequest(confirmed=True))
        assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            await main.change_incident_status(1, main.IncidentStatusRequest(status='NO_ACTION'))
        assert error.value.status_code == 409
    finally:
        release.set()
        await request
    with factory() as session:
        assert session.get(State, 'manual-git:1').value['status'] == 'SUCCEEDED'
    assert (await main.change_incident_status(1, main.IncidentStatusRequest(status='NO_ACTION')))['status'] == 'NO_ACTION'


def test_session_log_api_preserves_original_error_when_no_context(controls):
    client, factory, headers = controls
    with factory.begin() as session:
        session.get(Incident, 1).event = {'stack_trace': 'Original error', 'timestamp': '2026-10-17T00:00:00Z'}
    value = client.get('/incidents/1/session-log').json()
    assert value['unavailable'] and value['error']['text'] == 'Original error'
    assert value['before'] == value['after'] == []
    assert client.get('/incidents/999/session-log').status_code == 404


def test_local_mutation_and_origin_protection():
    client = TestClient(main.app, base_url='http://127.0.0.1')
    assert client.post('/fixes/1/deploy', json={'confirmed': True, 'patch_sha': 'a' * 64}).status_code == 403
    assert client.post('/fixes/1/deploy', headers={'X-Doctor-Action': '1', 'Origin': 'https://attacker.example'}, json={'confirmed': True, 'patch_sha': 'a' * 64}).status_code == 403
    assert client.post('/internal/logs', json={}).status_code == 403


def test_deploy_requires_approval_exact_diff_and_ready_tests(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'Session', factory)
    with factory.begin() as session:
        incident = Incident(fingerprint='test')
        session.add(incident)
        session.flush()
        session.add(FixJob(id=1, incident_id=incident.id, status='READY', patch_sha='a' * 64, tests={'passed': True}))
    client = TestClient(main.app, base_url='http://127.0.0.1')
    headers = {'X-Doctor-Action': '1'}
    assert client.post('/fixes/1/deploy', headers=headers, json={'confirmed': False, 'patch_sha': 'a' * 64}).status_code == 403
    assert client.post('/fixes/1/deploy', headers=headers, json={'confirmed': True, 'patch_sha': 'b' * 64}).status_code == 409
    assert client.post('/fixes/1/deploy', headers=headers, json={'confirmed': True, 'patch_sha': 'a' * 64}).status_code == 200
    assert client.post('/fixes/1/deploy', headers=headers, json={'confirmed': True, 'patch_sha': 'a' * 64}).status_code == 409


def test_explicit_continuation_does_not_reset_call_budget(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'Session', factory)
    monkeypatch.setattr(main, 'get_settings', lambda: type('Budget', (), {'max_agent_iterations': 4})())
    from doctor import fixing
    monkeypatch.setattr(fixing, 'Session', factory)
    with factory.begin() as session:
        incident = Incident(fingerprint='budget')
        session.add(incident)
        session.flush()
        job = FixJob(id=1, incident_id=incident.id, status='FAILED')
        session.add(job)
        session.flush()
        session.add(AgentRun(incident_id=incident.id, kind='fix', calls=4))
    client = TestClient(main.app, base_url='http://127.0.0.1')
    response = client.post('/fixes/1/continue', headers={'X-Doctor-Action': '1'})
    assert response.status_code == 409
    with factory.begin() as session:
        session.query(AgentRun).first().calls = 3
    response = client.post('/fixes/1/continue', headers={'X-Doctor-Action': '1'})
    assert response.status_code == 200 and response.json()['remaining_calls'] == 1


def test_superseded_job_cannot_escape_budget_attribution_window(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'Session', factory)
    with factory.begin() as session:
        incident = Incident(fingerprint='superseded')
        session.add(incident)
        session.flush()
        session.add(FixJob(id=1, incident_id=incident.id, status='FAILED'))
        session.flush()
        session.add(AgentRun(incident_id=incident.id, kind='fix', calls=3))
        session.flush()
        session.add(FixJob(id=2, incident_id=incident.id, status='FAILED'))
    client = TestClient(main.app, base_url='http://127.0.0.1')
    response = client.post('/fixes/1/continue', headers={'X-Doctor-Action': '1'})
    assert response.status_code == 409 and 'Superseded' in response.json()['detail']
