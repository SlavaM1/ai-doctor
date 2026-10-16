"""Deterministic, persistent log parsing. No model is called from the detector."""
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete
from .config import get_settings
from .db import Base, Session, State, Incident, Occurrence, Diagnosis, now
from sqlalchemy.orm import Mapped, mapped_column
from .security import redact

MAX_TRACE = 32768
LEVEL = re.compile(r'\b(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\b')
HEADER = re.compile(r'^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d')
EXCEPTION = re.compile(r'(?P<type>(?:[\w$]+\.)*[\w$]*(?:Exception|Error))(?![\w$])(?::\s*(?P<message>.*))?')
FRAME = re.compile(r'^\s*at\s+([\w.$/]+)\.([\w$<>]+)\(([^:()]+)(?::(\d+))?\)')
WARN_CRITICAL = re.compile(r'connection.*(?:fail|refused|reset|unavailable)|(?:fail|unable).*connect|pool.*(?:exhaust|timeout|not available)|deadlock|retry.*exhaust|timed?\s*out|timeout|health.*(?:down|fail)|external.*(?:fail|unavailable)|HTTP 5xx', re.I)
HTTP_5XX = re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+[^"\n]{1,2000}"\s+5\d\d\b')
CRITICAL = re.compile(r'APPLICATION FAILED TO START|OutOfMemoryError|HikariPool.*(?:Exception|failed)|SQL(?:SyntaxError|IntegrityConstraintViolation|NonTransientConnection)?Exception|BeanCreationException|CommunicationsException|Connection refused', re.I)


class LogReceipt(Base):
    __tablename__ = 'log_receipts'
    source_id: Mapped[str] = mapped_column(primary_key=True)
    seen_at: Mapped[str] = mapped_column(default=now, index=True)


def normalize(message: str) -> str:
    message = re.sub(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b', '<uuid>', message, flags=re.I)
    message = re.sub(r'\b\d{4}-\d\d-\d\d[T ][\d:.+Z-]+', '<time>', message)
    message = re.sub(r'\b(?:request|trace|correlation)[_-]?id\s*[=:]\s*\S+', 'requestId=<id>', message, flags=re.I)
    message = re.sub(r'0x[0-9a-f]+', '<address>', message, flags=re.I)
    message = re.sub(r'\b\d+\b', '<n>', message)
    return ' '.join(message.split())[:2000]


def parse_event(pending: dict) -> dict:
    lines = pending['lines']
    text = redact('\n'.join(lines))[:MAX_TRACE]
    level = LEVEL.search(lines[0])
    matches = list(EXCEPTION.finditer(text))
    if matches and matches[0].group('type') == 'Exception' and text.lstrip().startswith('Exception in thread'):
        matches = matches[1:] or matches
    frames = []
    for line in text.splitlines():
        match = FRAME.match(line)
        if match:
            cls, method, filename, lineno = match.groups()
            cls = cls.split('/')[-1]
            frames.append({'class': cls, 'method': method, 'filename': filename, 'line': int(lineno) if lineno else None})
    application = [f for f in frames if not f['class'].startswith(('java.', 'javax.', 'jdk.', 'sun.', 'org.springframework.', 'org.apache.', 'com.mysql.', 'org.hibernate.'))][:12]
    message = matches[0].group('message') if matches and matches[0].group('message') else lines[0]
    root = next((line.strip()[11:] for line in reversed(text.splitlines()) if line.strip().startswith('Caused by: ')), '')
    logger_match = re.search(r'\s([\w.$]+)\s*:\s', lines[0])
    return {'timestamp': pending['timestamp'], 'severity': level.group(1) if level else 'ERROR',
            'logger': logger_match.group(1) if logger_match else '',
            'exception_type': matches[0].group('type') if matches else '', 'message': redact(message),
            'root_cause': root, 'stack_trace': text, 'frames': frames[:80], 'application_frames': application,
            'service': pending['service'], 'container_id': pending['container_id'], 'version': pending.get('version', ''),
            'source_id': pending['source_id'], 'context': pending.get('context', [])[-8:]}


def fingerprint(event: dict) -> str:
    frames = event.get('application_frames', [])[:4]
    value = '|'.join([event['service'], event.get('version', ''), event.get('exception_type', ''),
                      normalize(event['message']), normalize(event.get('root_cause', '')),
                      repr([(f['class'], f['method'], f['filename']) for f in frames])])
    return hashlib.sha256(value.encode()).hexdigest()


class JavaParser:
    def __init__(self, state: dict | None = None):
        self.state = state or {'pending': None, 'context': []}

    def feed(self, record: dict, service: str, container_id: str, version: str) -> list[dict]:
        line = redact(record['text'])[:8192]
        access_error = HTTP_5XX.search(line)
        if access_error:
            line = f"{record['timestamp']} WARN HTTP 5xx response: {access_error.group(0)}"
        pending = self.state.get('pending')
        events = []
        header = bool(HEADER.match(line))
        independent_exception = bool(EXCEPTION.match(line)) and pending and any(EXCEPTION.match(x) for x in pending['lines'])
        if pending and (header or independent_exception):
            events.append(parse_event(pending))
            self.state['pending'] = pending = None
        level = LEVEL.search(line) if header else None
        starts = (level and level.group(1) in {'ERROR', 'FATAL', 'WARN'}) or (not header and EXCEPTION.match(line)) or 'APPLICATION FAILED TO START' in line
        if starts and pending is None:
            self.state['pending'] = {'lines': [line], 'timestamp': record['timestamp'], 'source_id': record['id'],
                                     'service': service, 'container_id': container_id, 'version': version,
                                     'context': self.state.get('context', [])[-8:], 'updated': time.time()}
        elif pending and not header:
            if sum(len(x) + 1 for x in pending['lines']) < MAX_TRACE:
                pending['lines'].append(line)
            pending['updated'] = time.time()
        self.state['context'] = (self.state.get('context', []) + [line])[-8:]
        return events

    def flush(self, force=False) -> list[dict]:
        pending = self.state.get('pending')
        if pending and (force or time.time() - pending['updated'] >= get_settings().multiline_idle_seconds):
            self.state['pending'] = None
            return [parse_event(pending)]
        return []


def significant(event: dict, session) -> bool:
    if event['severity'] in {'ERROR', 'FATAL'}:
        return True
    if event['severity'] != 'WARN':
        return False
    if not WARN_CRITICAL.search(event['stack_trace']):
        return False
    settings = get_settings()
    key = 'warn:' + fingerprint(event)
    row = session.get(State, key)
    current = time.time()
    timestamps = [t for t in (row.value.get('times', []) if row else []) if t >= current - settings.warn_window_seconds]
    timestamps.append(current)
    value = {'times': timestamps[-max(100, settings.warn_threshold):]}
    if row:
        row.value = value
    else:
        session.add(State(key=key, value=value))
    return len(timestamps) >= settings.warn_threshold


def store_event(session, event: dict) -> tuple[int | None, bool]:
    if not significant(event, session):
        return None, False
    if session.scalar(select(Occurrence.id).where(Occurrence.source_id == event['source_id'])):
        return None, False
    fp = fingerprint(event)
    incident = session.scalar(select(Incident).where(Incident.fingerprint == fp))
    created = incident is None
    if created:
        incident = Incident(fingerprint=fp, severity=event['severity'], exception_type=event['exception_type'],
                            message=event['message'], root_cause=event['root_cause'], service=event['service'],
                            version=event.get('version', ''), event=event)
        session.add(incident)
        session.flush()
    else:
        incident.occurrences += 1
        incident.last_seen = now()
        incident.event = event
        if incident.status == 'RESOLVED':
            incident.status = 'NEW'
            latest = session.scalar(select(Diagnosis.created_at).where(Diagnosis.incident_id == incident.id).order_by(Diagnosis.id.desc()).limit(1))
            due = (datetime.fromisoformat(latest).timestamp() + get_settings().diagnosis_cooldown_seconds) if latest else 0
            if due > time.time():
                incident.diagnosis_status = 'COOLDOWN'
                session.merge(State(key=f'cooldown:{incident.id}', value={'until': due}))
            else:
                incident.diagnosis_status = 'PENDING'
            created = True
    session.add(Occurrence(incident_id=incident.id, source_id=event['source_id']))
    return incident.id, created


def ingest_batch(payload: dict) -> dict:
    settings = get_settings()
    if payload['service'] not in settings.allowed_services.split(',') or payload['project'] != settings.compose_project:
        raise ValueError('Service/project is not allowlisted')
    records = payload.get('records', [])
    if len(records) > 1000:
        raise ValueError('Batch too large')
    key = 'parser:' + payload['container_id']
    new_incidents, changed = [], set()
    with Session.begin() as session:
        state = session.get(State, key)
        parser = JavaParser(state.value if state else None)
        for record in records:
            if session.get(LogReceipt, record['id']):
                continue
            session.add(LogReceipt(source_id=record['id']))
            for event in parser.feed(record, payload['service'], payload['container_id'], payload.get('version', '')):
                incident_id, created = store_event(session, event)
                if incident_id:
                    changed.add(incident_id)
                if created:
                    new_incidents.append(incident_id)
        for event in parser.flush():
            incident_id, created = store_event(session, event)
            if incident_id:
                changed.add(incident_id)
            if created:
                new_incidents.append(incident_id)
        if state:
            state.value = {**parser.state, 'last_seen_at': time.time()}
        else:
            session.add(State(key=key, value={**parser.state, 'last_seen_at': time.time()}))
        heartbeat = session.get(State, 'collector')
        value = {'last_seen': now(), 'container_id': payload['container_id'], 'service': payload['service'], 'cursor': payload.get('cursor', ''), 'status': 'connected'}
        if heartbeat:
            heartbeat.value = value
        else:
            session.add(State(key='collector', value=value))
    return {'new_incidents': new_incidents, 'changed': list(changed), 'accepted': len(records)}


def flush_pending() -> dict:
    """Finalize idle buffers even if a container was removed before the next poll."""
    changed, created = [], []
    with Session.begin() as session:
        for state in session.scalars(select(State).where(State.key.like('parser:%'))):
            if not state.value.get('pending'):
                continue
            parser = JavaParser(dict(state.value))
            events = parser.flush()
            if not events:
                continue
            state.value = dict(parser.state)
            for event in events:
                incident_id, new = store_event(session, event)
                if incident_id:
                    changed.append(incident_id)
                if new:
                    created.append(incident_id)
    return {'changed': changed, 'new_incidents': created}


def release_cooldowns() -> list[int]:
    changed = []
    with Session.begin() as session:
        for state in session.scalars(select(State).where(State.key.like('cooldown:%'))):
            incident = session.get(Incident, int(state.key.split(':')[1]))
            if not incident or incident.diagnosis_status != 'COOLDOWN':
                session.delete(state)
            elif state.value.get('until', 0) <= time.time():
                incident.diagnosis_status = 'PENDING'
                session.delete(state)
                changed.append(incident.id)
    return changed


def retention():
    settings = get_settings()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.retention_days)).isoformat()
    receipts_cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with Session.begin() as session:
        session.execute(delete(Occurrence).where(Occurrence.seen_at < cutoff))
        session.execute(delete(LogReceipt).where(LogReceipt.seen_at < receipts_cutoff))
        # Parser buffers belong to containers; don't retain retired container logs forever.
        for row in session.scalars(select(State).where(State.key.like('parser:%'))):
            if not row.value.get('pending') and row.value.get('last_seen_at', time.time()) < time.time() - 3 * 86400:
                session.delete(row)
        for row in session.scalars(select(State).where(State.key.like('warn:%'))):
            if max(row.value.get('times', [0]), default=0) < time.time() - 3600:
                session.delete(row)
