"""Conservative error families: exact normalized evidence, never fuzzy similarity."""
import hashlib
import json
import re


def stable_text(value):
    # Standard Spring header contains volatile PID/thread metadata, not the error.
    value = re.sub(r'^\d{4}-\d\d-\d\d[T ][\d:.+Z-]+\s+(?:ERROR|FATAL|WARN)\s+\d+\s+---\s+\[[^\]]+\]\s+[\w.$]+\s*:\s*', '', value)
    value = re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '<uuid>', value, flags=re.I)
    value = re.sub(r'\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?', '<time>', value)
    value = re.sub(r'\b(request|trace|correlation)[_-]?id\s*[=:]\s*[\w.:-]+', r'\1Id=<id>', value, flags=re.I)
    return ' '.join(value.split())


def signature(event):
    # Keep numbers, HTTP codes, paths, resource names, case, and severity intact.
    # Image version and frame line numbers can change without changing the cause.
    evidence = {key: stable_text(str(event.get(key, ''))) for key in
                ('service', 'severity', 'exception_type', 'message', 'root_cause', 'logger')}
    evidence['frames'] = [{key: frame.get(key) for key in ('class', 'method', 'filename')}
                          for frame in event.get('application_frames', [])[:4]]
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
