from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from doctor import main
from doctor.db import Base, Incident, FixJob, AgentRun
import pytest


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


@pytest.mark.parametrize('active', ['diagnosis', 'fix', 'deployment', 'unknown-deployment'])
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
                session.add(Deployment(fix_job_id=1, status='UNKNOWN' if active.startswith('unknown') else 'DEPLOYING'))
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
    client, factory, headers = controls
    calls = []
    async def runner(operation, payload, timeout):
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
