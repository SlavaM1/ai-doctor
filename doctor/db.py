from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, event, String, Text, Integer, Float, JSON, ForeignKey, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import get_settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = 'incidents'
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    severity: Mapped[str] = mapped_column(default='ERROR')
    status: Mapped[str] = mapped_column(default='NEW')
    diagnosis_status: Mapped[str] = mapped_column(default='PENDING')
    exception_type: Mapped[str] = mapped_column(default='')
    message: Mapped[str] = mapped_column(Text, default='')
    root_cause: Mapped[str] = mapped_column(Text, default='')
    service: Mapped[str] = mapped_column(default='app')
    version: Mapped[str] = mapped_column(default='')
    first_seen: Mapped[str] = mapped_column(default=now)
    last_seen: Mapped[str] = mapped_column(default=now)
    occurrences: Mapped[int] = mapped_column(default=1)
    event: Mapped[dict] = mapped_column(JSON, default=dict)


class Occurrence(Base):
    __tablename__ = 'incident_occurrences'
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey('incidents.id'), index=True)
    seen_at: Mapped[str] = mapped_column(default=now, index=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True)


class Diagnosis(Base):
    __tablename__ = 'diagnoses'
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey('incidents.id'), index=True)
    created_at: Mapped[str] = mapped_column(default=now)
    model: Mapped[str] = mapped_column(default='')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default='')


class FixJob(Base):
    __tablename__ = 'fix_jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey('incidents.id'), index=True)
    created_at: Mapped[str] = mapped_column(default=now)
    status: Mapped[str] = mapped_column(default='QUEUED')
    model: Mapped[str] = mapped_column(default='')
    goal: Mapped[str] = mapped_column(Text, default='')
    base_commit: Mapped[str] = mapped_column(default='')
    workspace: Mapped[str] = mapped_column(default='')
    patch: Mapped[str] = mapped_column(Text, default='')
    patch_sha: Mapped[str] = mapped_column(default='')
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    events: Mapped[list] = mapped_column(JSON, default=list)
    tests: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default='')
    error: Mapped[str] = mapped_column(Text, default='')


class Deployment(Base):
    __tablename__ = 'deployments'
    id: Mapped[int] = mapped_column(primary_key=True)
    fix_job_id: Mapped[int] = mapped_column(ForeignKey('fix_jobs.id'))
    created_at: Mapped[str] = mapped_column(default=now)
    status: Mapped[str] = mapped_column(default='QUEUED')
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentRun(Base):
    __tablename__ = 'agent_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(default='diagnosis')
    model: Mapped[str] = mapped_column(default='')
    created_at: Mapped[str] = mapped_column(default=now)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    calls: Mapped[int] = mapped_column(default=1)
    latency_ms: Mapped[int] = mapped_column(default=0)
    approximate: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str] = mapped_column(Text, default='')


class State(Base):
    __tablename__ = 'state'
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class ExclusionRule(Base):
    __tablename__ = 'exclusion_rules'
    signature: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(default=now)
    sample: Mapped[dict] = mapped_column(JSON)


class User(Base):
    __tablename__ = 'users'
    username: Mapped[str] = mapped_column(primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey('users.username'))
    expires_at: Mapped[str] = mapped_column(index=True)


class SessionLogEvent(Base):
    __tablename__ = 'session_log_events'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True)
    container_id: Mapped[str] = mapped_column(String(128), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), default='', index=True)
    timestamp: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=now)


class IncidentLogWindow(Base):
    __tablename__ = 'incident_log_windows'
    incident_id: Mapped[int] = mapped_column(ForeignKey('incidents.id'), primary_key=True)
    container_id: Mapped[str] = mapped_column(String(128), index=True)
    anchor_id: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(128), default='')
    collecting: Mapped[bool] = mapped_column(default=True, index=True)
    expires_at: Mapped[str] = mapped_column(default=lambda: (datetime.now(timezone.utc) + timedelta(days=3)).isoformat())
    data: Mapped[dict] = mapped_column(JSON, default=dict)


engine = create_engine(get_settings().database_url, connect_args={'check_same_thread': False, 'timeout': 30})


@event.listens_for(engine, 'connect')
def sqlite_options(connection, _):
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')


Session = sessionmaker(engine, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)


def serialize(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def next_id(session, model):
    """Never reuse deleted IDs, including trusted runner workspace job IDs."""
    key = 'id-sequence:' + model.__tablename__
    state = session.get(State, key)
    value = max(session.scalar(select(func.max(model.id))) or 0, state.value.get('last', 0) if state else 0) + 1
    session.merge(State(key=key, value={'last': value}))
    return value
