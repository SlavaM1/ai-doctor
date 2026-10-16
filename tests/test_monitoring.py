import time
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from doctor import monitoring as m
from doctor.db import Base, Incident, Occurrence, State, Diagnosis


@pytest.fixture
def store(monkeypatch):
    cfg = m.get_settings().model_copy(update={'allowed_services': 'app'})
    monkeypatch.setattr(m, 'get_settings', lambda: cfg)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(m, 'Session', factory)
    return factory


def record(text, index):
    return {'id': str(index), 'text': text, 'timestamp': '2026-10-16T18:00:00Z'}


def java_event(identifier='123', source='event1'):
    parser = m.JavaParser()
    lines = [f'2026-10-16 18:00:00 ERROR 1 --- [main] com.aurel.Service : Failed retry for id {identifier}',
             f'java.lang.IllegalStateException: Failed retry for id {identifier}',
             '\tat com.aurel.Service.save(Service.java:42)',
             'Caused by: java.sql.SQLException: connection refused',
             '\tat com.aurel.Repository.save(Repository.java:21)']
    events = []
    for index, line in enumerate(lines):
        events += parser.feed(record(line, source + str(index)), 'app', 'abc', 'rev')
    events += parser.flush(force=True)
    return events[0]


def test_multiline_exception_root_and_frames():
    event = java_event()
    assert event['exception_type'] == 'java.lang.IllegalStateException'
    assert event['logger'] == 'com.aurel.Service'
    assert event['application_frames'][0]['line'] == 42
    assert event['root_cause'] == 'java.sql.SQLException: connection refused'
    assert event['stack_trace'].count('Caused by') == 1


def test_info_debug_and_independent_errors():
    parser = m.JavaParser()
    for i, level in enumerate(['INFO', 'DEBUG', 'TRACE']):
        assert not parser.feed(record(f'2026-10-16 18:00:00 {level} normal', i), 'app', 'abc', '')
    assert parser.flush(True) == []
    parser.feed(record('2026-10-16 18:00:00 ERROR first', 4), 'app', 'abc', '')
    result = parser.feed(record('2026-10-16 18:00:01 ERROR second', 5), 'app', 'abc', '')
    assert len(result) == 1
    assert len(parser.flush(True)) == 1


def test_error_with_retry_never_ignored(store):
    with store.begin() as session:
        assert m.significant(java_event(), session)


def test_warn_threshold_but_harmless_warn_ignored(store):
    event = java_event()
    event['severity'] = 'WARN'
    event['stack_trace'] = 'WARN Connection failed to database'
    with store.begin() as session:
        assert not m.significant(event, session)
        assert not m.significant(event, session)
        assert m.significant(event, session)
        event['stack_trace'] = 'WARN Open in view enabled'
        assert not m.significant(event, session)


def test_stable_fingerprint_normalizes_ids_uuid():
    assert m.fingerprint(java_event('123')) == m.fingerprint(java_event('999'))
    assert m.normalize('id=132 d8ee9022-3caa-4896-a324-652b1ddf7909') == 'id=<n> <uuid>'


def test_dedup_persistence_and_reopen(store):
    new_count = 0
    with store.begin() as session:
        for i in range(10):
            incident_id, new = m.store_event(session, java_event(str(i), str(i)))
            new_count += new
        assert new_count == 1
    with store.begin() as session:
        incidents = list(session.scalars(select(Incident)))
        assert len(incidents) == 1 and incidents[0].occurrences == 10
        incidents[0].status = 'RESOLVED'
        _, new = m.store_event(session, java_event('100', 'new-after-fix'))
        assert new and incidents[0].status == 'NEW'


def test_batch_retry_checkpoint_and_parser_restoration(store):
    payload = {'project': 'day35-aurelspa', 'service': 'app', 'container_id': 'cid', 'version': 'rev', 'records': [
        record('2026-10-16 18:00:00 ERROR failure', 1),
        record('java.lang.NullPointerException: bad', 2)]}
    assert m.ingest_batch(payload)['accepted'] == 2
    m.ingest_batch(payload)  # at-least-once delivery does not duplicate pending lines
    payload['records'] = [record('\tat com.aurel.Service.save(Service.java:42)', 3), record('2026-10-16 18:00:01 INFO done', 4)]
    result = m.ingest_batch(payload)
    assert len(result['new_incidents']) == 1
    m.ingest_batch(payload)
    with store() as session:
        incident = session.scalar(select(Incident))
        assert incident.occurrences == 1
        assert incident.event['stack_trace'].count('NullPointerException') == 1


def test_service_allowlist(store):
    with pytest.raises(ValueError, match='allowlisted'):
        m.ingest_batch({'project': 'other-project', 'service': 'app', 'container_id': 'abc', 'records': []})


def test_idle_buffer_finalizes_without_container_poll(store):
    parser = m.JavaParser()
    parser.feed(record('2026-10-16 18:00:00 ERROR terminated', 1), 'app', 'removed', '')
    parser.state['pending']['updated'] = time.time() - 5
    with store.begin() as session:
        session.add(State(key='parser:removed', value=parser.state))
    assert len(m.flush_pending()['new_incidents']) == 1
    assert m.flush_pending()['new_incidents'] == []


def test_reopened_incident_observes_persistent_diagnosis_cooldown(store):
    with store.begin() as session:
        incident_id, _ = m.store_event(session, java_event(source='initial'))
        session.add(Diagnosis(incident_id=incident_id))
        session.get(Incident, incident_id).status = 'RESOLVED'
    with store.begin() as session:
        m.store_event(session, java_event(source='reopened'))
        assert session.get(Incident, incident_id).diagnosis_status == 'COOLDOWN'
    assert m.release_cooldowns() == []
    with store.begin() as session:
        session.get(State, f'cooldown:{incident_id}').value = {'until': 0}
    assert m.release_cooldowns() == [incident_id]
    assert m.release_cooldowns() == []


def test_trace_is_size_bounded():
    parser = m.JavaParser()
    parser.feed(record('2026-10-16 18:00:00 ERROR failure', 0), 'app', 'abc', '')
    for i in range(100):
        parser.feed(record('  at ' + 'x' * 8000, i + 1), 'app', 'abc', '')
    assert len(parser.flush(True)[0]['stack_trace']) <= m.MAX_TRACE
