"""Bounded redacted event ring and one durable 5/error/5 snapshot per incident."""
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete
from .db import SessionLogEvent, IncidentLogWindow, now
from .monitoring import HEADER, EXCEPTION, HTTP_5XX
from .security import redact

TRACE_ID = re.compile(r'\btraceId["\s]*[=:]\s*["\']?([A-Za-z0-9._:-]{1,128})(?=[\s"\',}\]]|$)', re.I)
RING_LIMIT = 5000
TEXT_LIMIT = 8192


def bounded_text(text):
    return text.encode('utf-8')[:TEXT_LIMIT].decode('utf-8', 'ignore')


def capture(session, record, container_id):
    text = bounded_text(redact(record['text']))
    trace = TRACE_ID.search(text)
    previous = session.scalar(select(SessionLogEvent).where(SessionLogEvent.container_id == container_id).order_by(SessionLogEvent.id.desc()).limit(1))
    independent_exception = previous and EXCEPTION.match(text) and EXCEPTION.match(previous.text)
    if previous and not HEADER.match(text) and not text.startswith('{') and not trace and not independent_exception and not HTTP_5XX.search(text):
        previous.text = bounded_text(previous.text + '\n' + text)
    else:
        session.add(SessionLogEvent(source_id=record['id'], container_id=container_id,
                                    trace_id=trace.group(1) if trace else '',
                                    timestamp=str(record['timestamp'])[:100], text=text))
    session.flush()


def snapshot(session, incident_id, event):
    anchor = session.scalar(select(SessionLogEvent).where(SessionLogEvent.source_id == event['source_id']))
    if anchor:
        session.merge(IncidentLogWindow(incident_id=incident_id, container_id=anchor.container_id,
                                       anchor_id=anchor.id, trace_id=anchor.trace_id, data={}, collecting=True,
                                       expires_at=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat()))


def refresh(session, container_id):
    changed = []
    for window in session.scalars(select(IncidentLogWindow).where(IncidentLogWindow.container_id == container_id,
                                                                  IncidentLogWindow.collecting.is_(True),
                                                                  IncidentLogWindow.expires_at >= now()).limit(RING_LIMIT)):
        data = window.data
        # Completed snapshots no longer depend on the transient ring.
        if len(data.get('after', [])) == 5 and data.get('complete'):
            continue
        query = select(SessionLogEvent).where(SessionLogEvent.container_id == container_id)
        if window.trace_id:
            query = query.where(SessionLogEvent.trace_id == window.trace_id)
        before = list(session.scalars(query.where(SessionLogEvent.id < window.anchor_id).order_by(SessionLogEvent.id.desc()).limit(5)))[::-1]
        after = list(session.scalars(query.where(SessionLogEvent.id > window.anchor_id).order_by(SessionLogEvent.id).limit(5)))
        anchor = session.get(SessionLogEvent, window.anchor_id)
        def item(row):
            return {'event_id': row.id, 'timestamp': row.timestamp, 'text': row.text, 'trace_id': row.trace_id}
        if anchor or data.get('error'):
            latest = session.scalar(select(SessionLogEvent.id).where(SessionLogEvent.container_id == container_id).order_by(SessionLogEvent.id.desc()).limit(1))
            neighbors = {entry['event_id']: entry for entry in data.get('after', [])}
            neighbors.update({row.id: item(row) for row in after})
            following = [neighbors[key] for key in sorted(neighbors)[:5]]
            value = {'trace_id': window.trace_id, 'before': data.get('before', [item(row) for row in before]),
                     'error': item(anchor) if anchor else data['error'], 'after': following,
                     'complete': len(following) == 5 and following[-1]['event_id'] < (latest or 0)}
            if value != data:
                window.data = value
                window.collecting = not value['complete']
                changed.append(window.incident_id)
    boundary = session.scalar(select(SessionLogEvent.id).order_by(SessionLogEvent.id.desc()).offset(RING_LIMIT - 1).limit(1))
    if boundary:
        session.execute(delete(SessionLogEvent).where(SessionLogEvent.id < boundary))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    session.execute(delete(SessionLogEvent).where(SessionLogEvent.created_at < cutoff))
    return changed
