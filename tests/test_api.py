from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from doctor import main
from doctor.db import Base, Incident, FixJob, AgentRun


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
