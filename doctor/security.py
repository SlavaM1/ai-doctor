"""Redaction at every boundary that can contain application or model data."""

import re
from typing import Any


_SECRET_NAME = r'(?:[a-z0-9_-]*(?:api[-_]?key|access[-_]?token|refresh[-_]?token|client[-_]?secret|password|passwd|pwd|username|secret|token|authorization|auth|cookie)|set-cookie)'
_SECRET_KEY = re.compile(rf'^{_SECRET_NAME}$', re.I)
_QUOTED = re.compile(
    rf'(?P<prefix>["\']?\b{_SECRET_NAME}["\']?\s*[:=]\s*)(?P<quote>["\'])(?:\\.|(?!(?P=quote)).)*(?P=quote)',
    re.I,
)
_HEADER = re.compile(r'(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)(?!["\'])[^\r\n]+')
_VALUE = re.compile(rf'(\b{_SECRET_NAME}\s*[:=]\s*)(?!["\'])([^\s,;}}&]+)', re.I)
_BEARER = re.compile(r'\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', re.I)
_URL_AUTH = re.compile(r'(\b[a-z][a-z0-9+.-]*://)[^\s/@]+(?::[^\s/@]*)?@', re.I)
_JDBC_USER = re.compile(r'([?;&](?:user|username)=)[^\s;&]+', re.I)
_ORACLE_AUTH = re.compile(r'(jdbc:oracle:thin:)[^\s/@:]+/[^\s@]+@', re.I)
_EMAIL = re.compile(r'(?<![\w.+-])[A-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.[A-Z]{2,}(?![\w.-])', re.I)
_API_KEY = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{8,}|AKIA[A-Z0-9]{16})\b')


def redact(value: str) -> str:
    """Remove credential values, not diagnostic types or Java stack frames."""
    value = _QUOTED.sub(lambda m: m['prefix'] + m['quote'] + '[REDACTED]' + m['quote'], value)
    value = _HEADER.sub(r'\1[REDACTED]', value)
    value = _BEARER.sub(r'\1 [REDACTED]', value)
    value = _VALUE.sub(r'\1[REDACTED]', value)
    value = _URL_AUTH.sub(r'\1[REDACTED]@', value)
    value = _JDBC_USER.sub(r'\1[REDACTED]', value)
    value = _ORACLE_AUTH.sub(r'\1[REDACTED]@', value)
    value = _API_KEY.sub('[REDACTED]', value)
    return _EMAIL.sub('[REDACTED_EMAIL]', value)


def redact_data(value: Any) -> Any:
    """Return a redacted copy of a JSON-like structure without mutating it."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {
            key: '[REDACTED]' if isinstance(key, str) and _SECRET_KEY.fullmatch(key) else redact_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_data(item) for item in value]
    return value
