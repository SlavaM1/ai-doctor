"""Local accounts and revocable, server-side browser sessions."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete
from .db import User, LoginSession, now

COOKIE = 'doctor_session'
SESSION_SECONDS = 12 * 60 * 60


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f'{salt}:{digest}'


def seed_admin(session):
    if not session.get(User, 'admin'):
        session.add(User(username='admin', password_hash=password_hash('admin')))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(session, token):
    if not token or len(token) > 128:
        return None
    login = session.get(LoginSession, token_hash(token))
    return login.username if login and login.expires_at > now() and session.get(User, login.username) else None


def login(session, username, password):
    user = session.get(User, username)
    # Perform the same expensive hash for unknown accounts as well.
    stored = user.password_hash if user else '0' * 32 + ':' + '0' * 128
    valid = hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)
    if not user or not valid:
        return None
    session.execute(delete(LoginSession).where(LoginSession.expires_at <= now()))
    token = secrets.token_urlsafe(32)
    session.add(LoginSession(token_hash=token_hash(token), username=username,
                             expires_at=(datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)).isoformat()))
    return token
