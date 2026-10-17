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


def test_exclusion_signature_only_normalizes_known_volatile_values():
    from doctor.exclusions import signature
    from copy import deepcopy
    event = java_event('123')
    event['message'] = 'HTTP 404 /orders/123 requestId=abc d8ee9022-3caa-4896-a324-652b1ddf7909 at 2026-10-16T18:00:00Z'
    same = deepcopy(event)
    same['version'] = 'new-image'
    same['application_frames'][0]['line'] = 99
    same['message'] = event['message'].replace('abc', 'def').replace('d8ee9022', 'a8ee9022').replace('18:00:00', '19:01:00')
    assert signature(same) == signature(event)
    changes = [('message', event['message'].replace('404', '500')),
               ('message', event['message'].replace('/orders/123', '/orders/999')),
               ('message', event['message'].replace('HTTP', 'http')),
               ('root_cause', 'new cause'), ('service', 'other'),
               ('logger', 'other.logger'), ('severity', 'FATAL'), ('exception_type', 'java.lang.NullPointerException'),
               ('application_frames', [])]
    for key, value in changes:
        assert signature({**event, key: value}) != signature(event)
    header = '2026-10-16 18:00:00 ERROR 1 --- [nio-8080-exec-1] com.aurel.Service : HTTP 404 /orders'
    other = header.replace('ERROR 1 ---', 'ERROR 99 ---').replace('exec-1', 'exec-3')
    assert signature({**event, 'message': header}) == signature({**event, 'message': other})


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


def test_no_action_never_reopens_or_schedules_diagnosis(store):
    with store.begin() as session:
        incident_id, _ = m.store_event(session, java_event(source='initial'))
        incident = session.get(Incident, incident_id)
        incident.status, incident.diagnosis_status = 'NO_ACTION', 'SKIPPED'
        session.add(State(key=f'cooldown:{incident_id}', value={'until': 0}))
    with store.begin() as session:
        _, created = m.store_event(session, java_event(source='repeat'))
        incident = session.get(Incident, incident_id)
        assert not created and incident.status == 'NO_ACTION' and incident.diagnosis_status == 'SKIPPED'
        assert incident.occurrences == 2
    assert m.release_cooldowns() == []


@pytest.mark.parametrize('status', ['NO_ACTION', 'EXCLUDED'])
def test_ignored_warn_repeats_below_threshold_are_stored_once(store, status):
    from doctor.db import ExclusionRule
    from doctor.exclusions import signature
    event = {**java_event(source='ignored-warn'), 'severity': 'WARN', 'stack_trace': 'WARN Connection failed'}
    with store.begin() as session:
        # Initially admitted by the ordinary WARN threshold.
        assert m.store_event(session, event) == (None, False)
        assert m.store_event(session, event) == (None, False)
        incident_id, created = m.store_event(session, event)
        assert created
        incident = session.get(Incident, incident_id)
        incident.status, incident.diagnosis_status = status, 'SKIPPED'
        incident.last_seen = '2000-01-01T00:00:00+00:00'
        session.delete(session.get(State, 'warn:' + m.fingerprint(event)))
        if status == 'EXCLUDED':
            session.add(ExclusionRule(signature=signature(event), sample=event))
    with store.begin() as session:
        repeat = {**event, 'source_id': 'warn-repeat'}
        assert m.store_event(session, repeat) == (incident_id, False)
        assert m.store_event(session, repeat) == (None, False)
        incident = session.get(Incident, incident_id)
        assert incident.status == status and incident.diagnosis_status == 'SKIPPED'
        assert incident.occurrences == 2 and incident.last_seen != '2000-01-01T00:00:00+00:00'
        assert session.query(Occurrence).filter_by(incident_id=incident_id).count() == 2
        upgraded, queued = m.store_event(session, {**event, 'source_id': 'upgrade', 'version': 'new-version'})
        if status == 'EXCLUDED':
            assert upgraded != incident_id and not queued
            assert session.get(Incident, upgraded).status == 'EXCLUDED'
            assert session.get(Incident, upgraded).diagnosis_status == 'SKIPPED'
        else:
            # NO_ACTION is scoped to the existing versioned fingerprint, not a family rule.
            assert upgraded is None and not queued


def test_no_action_does_not_suppress_new_version(store):
    with store.begin() as session:
        incident_id, _ = m.store_event(session, java_event())
        incident = session.get(Incident, incident_id)
        incident.status, incident.diagnosis_status = 'NO_ACTION', 'SKIPPED'
        upgraded, queued = m.store_event(session, {**java_event(source='upgrade'), 'version': 'new-version'})
        assert upgraded != incident_id and queued
        assert session.get(Incident, upgraded).diagnosis_status == 'PENDING'


def test_session_log_five_neighbors_grouping_multiline_and_later_batches(store):
    from doctor.db import IncidentLogWindow, SessionLogEvent
    def entry(index, level='INFO', trace='session-a', text='event'):
        return record(f'2026-10-16 18:00:00 {level} traceId={trace} {text} {index}', str(index))
    payload = {'project': 'day35-aurelspa', 'service': 'app', 'container_id': 'cid', 'records': []}
    payload['records'] = [entry(i) for i in range(8)] + [entry(8, trace='other')] + [entry(9, 'ERROR', text='password=hidden-value failure')]
    m.ingest_batch(payload)
    payload['records'] = [record('java.lang.IllegalStateException: broken', 'stack'), entry(10), entry(11, trace='other')]
    m.ingest_batch(payload)
    with store() as session:
        incident = session.scalar(select(Incident))
        incident_id = incident.id
        data = session.get(IncidentLogWindow, incident_id).data
        assert len(data['before']) == 5 and len(data['after']) == 1
        assert all(item['trace_id'] == 'session-a' for item in data['before'] + data['after'])
        assert data['before'][0]['text'].endswith('event 3')
        assert 'IllegalStateException' in data['error']['text']
        assert 'hidden-value' not in repr(data)
    payload['records'] = [entry(i) for i in range(12, 20)]
    changed = m.ingest_batch(payload)['changed']
    assert incident_id in changed
    m.ingest_batch(payload)  # retries do not duplicate context
    with store() as session:
        data = session.get(IncidentLogWindow, incident_id).data
        assert len(data['after']) == 5 and data['after'][-1]['text'].endswith('event 15')
        assert session.query(SessionLogEvent).count() == 20


def test_session_log_fallback_ring_bound_and_snapshot_survives_pruning(store, monkeypatch):
    from doctor import session_logs
    from doctor.db import SessionLogEvent, IncidentLogWindow
    monkeypatch.setattr(session_logs, 'RING_LIMIT', 8)
    payload = {'project': 'day35-aurelspa', 'service': 'app', 'container_id': 'cid', 'records': [
        record(f'2026-10-16 18:00:00 {"ERROR" if i == 3 else "INFO"} event {i}', str(i)) for i in range(10)]}
    m.ingest_batch(payload)
    with store() as session:
        data = session.scalar(select(IncidentLogWindow)).data
        assert not data['trace_id'] and len(data['before']) == 3 and len(data['after']) == 5
        assert session.query(SessionLogEvent).count() == 8
    payload['records'] = [record(f'2026-10-16 18:00:00 INFO later {i}', str(i)) for i in range(10, 30)]
    m.ingest_batch(payload)
    with store() as session:
        assert session.scalar(select(IncidentLogWindow)).data == data
        assert session.query(SessionLogEvent).count() == 8


def test_aurelspa_mdc_bracket_uuid_and_empty_trace_id(store):
    from doctor.db import IncidentLogWindow
    from doctor.session_logs import TRACE_ID
    trace = '3c49872e-94ea-458c-b4e0-621f77c9e8ec'
    assert TRACE_ID.search(f'[traceId={trace}]').group(1) == trace
    assert TRACE_ID.search('[traceId=]') is None
    payload = {'project': 'day35-aurelspa', 'service': 'app', 'container_id': 'cid', 'records': [
        record(f'2026-10-17 10:00:00.100 INFO [http-1] [traceId={trace}] com.aurel.Service : request started', '1'),
        record('2026-10-17 10:00:00.101 INFO [main] [traceId=] com.aurel.Service : background work', '2'),
        record(f'2026-10-17 10:00:00.102 ERROR [http-1] [traceId={trace}] com.aurel.Service : request failed', '3'),
        record('java.lang.IllegalStateException: bad input', '4'),
        record(f'2026-10-17 10:00:00.103 INFO [http-2] [traceId={trace}] com.aurel.Service : request completed', '5'),
    ]}
    m.ingest_batch(payload)
    with store() as session:
        data = session.scalar(select(IncidentLogWindow)).data
        assert data['trace_id'] == trace
        assert len(data['before']) == len(data['after']) == 1
        assert 'background work' not in repr(data)
        assert 'IllegalStateException' in data['error']['text']


def test_later_trace_logs_fill_snapshot_even_after_anchor_evicted(store, monkeypatch):
    from doctor import session_logs
    from doctor.db import IncidentLogWindow, SessionLogEvent
    monkeypatch.setattr(session_logs, 'RING_LIMIT', 8)
    payload = {'project': 'day35-aurelspa', 'service': 'app', 'container_id': 'cid', 'records': [
        record('2026-10-17 10:00:00 INFO [traceId=target] before', 'before'),
        record('2026-10-17 10:00:00 ERROR [traceId=target] error', 'error'),
        record('2026-10-17 10:00:00 INFO [traceId=target] first after', 'after'),
    ]}
    m.ingest_batch(payload)
    payload['records'] = [record(f'2026-10-17 10:00:00 INFO [traceId=unrelated] event {i}', str(i)) for i in range(20)]
    m.ingest_batch(payload)
    with store() as session:
        assert not session.scalar(select(SessionLogEvent).where(SessionLogEvent.source_id == 'error'))
    payload['records'] = [record(f'2026-10-17 10:00:00 INFO [traceId=target] later {i}', str(i)) for i in range(20, 26)]
    m.ingest_batch(payload)
    with store() as session:
        data = session.scalar(select(IncidentLogWindow)).data
        assert len(data['before']) == 1 and len(data['after']) == 5
        assert 'first after' in data['after'][0]['text']
        assert 'later 23' in data['after'][-1]['text']
