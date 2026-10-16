"""Trusted host-only collector: fixed Docker read operations, durable at-least-once batches."""
import asyncio
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
from .config import get_settings
from .security import redact

RUNTIME = Path('.runtime')


def atomic_json(path: Path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


async def docker(*args):
    process = await asyncio.create_subprocess_exec('docker', *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError('Docker read timed out')
    if process.returncode:
        raise RuntimeError('Docker read failed')
    return stdout.decode(errors='replace')


async def target_containers(service):
    cfg = get_settings()
    ids = (await docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={cfg.compose_project}', '--filter', f'label=com.docker.compose.service={service}')).split()
    if not ids:
        return []
    containers = json.loads(await docker('inspect', *ids))
    # Include stopped containers so shutdown/startup errors are not lost during replacement.
    return sorted(containers, key=lambda c: c['Created'])


def overlap(timestamp):
    try:
        return (datetime.fromisoformat(timestamp.replace('Z', '+00:00')) - timedelta(seconds=2)).isoformat()
    except ValueError:
        return timestamp


async def main():
    cfg = get_settings()
    RUNTIME.mkdir(exist_ok=True, mode=0o700)
    path = RUNTIME / 'collector-checkpoints.json'
    spool = RUNTIME / 'collector-pending.json'
    checkpoints = json.loads(path.read_text()) if path.exists() else {}
    headers = {'X-Runner-Token': cfg.runner_token}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        async def deliver(payload):
            atomic_json(spool, payload)
            response = await client.post(cfg.api_url + '/internal/logs', json=payload)
            response.raise_for_status()
            checkpoints[payload['container_id']] = payload['cursor']
            atomic_json(path, checkpoints)
            spool.unlink(missing_ok=True)

        while True:
            try:
                if spool.exists():
                    await deliver(json.loads(spool.read_text()))
                for service in cfg.allowed_services.split(','):
                    for container in await target_containers(service):
                        cid = container['Id']
                        cursor = checkpoints.get(cid, container['Created'])
                        image = json.loads(await docker('image', 'inspect', container['Image']))[0]
                        labels = image.get('Config', {}).get('Labels') or {}
                        version = labels.get('org.opencontainers.image.revision', '') or container['Image']
                        if labels.get('doctor.patch'):
                            version += ':' + labels['doctor.patch']
                        proc = await asyncio.create_subprocess_exec('docker', 'logs', '--timestamps', '--since', overlap(cursor), cid,
                                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=1024 * 1024)
                        records, repeated = [], defaultdict(int)
                        last = cursor
                        async def send():
                            await deliver({'project': cfg.compose_project, 'service': service, 'container_id': cid,
                                           'version': version, 'cursor': last, 'records': list(records)})
                        try:
                            while True:
                                raw = await asyncio.wait_for(proc.stdout.readline(), 30)
                                if not raw:
                                    break
                                line = raw.decode(errors='replace').rstrip('\r\n')
                                stamp, separator, text = line.partition(' ')
                                if not separator or not stamp.endswith('Z'):
                                    continue
                                signature = hashlib.sha256((stamp + text).encode()).hexdigest()
                                index = repeated[signature]
                                repeated[signature] += 1
                                source_id = hashlib.sha256(f'{cid}:{signature}:{index}'.encode()).hexdigest()
                                records.append({'id': source_id, 'timestamp': stamp, 'text': redact(text)[:8192]})
                                last = max(last, stamp)
                                if len(records) >= 300:
                                    await send()
                                    records.clear()
                            await proc.wait()
                            if proc.returncode:
                                raise RuntimeError('Docker log stream interrupted')
                            state = container.get('State', {})
                            health = state.get('Health', {})
                            if health.get('Status') == 'unhealthy':
                                for probe in health.get('Log', [])[-5:]:
                                    if probe.get('ExitCode', 0) == 0:
                                        continue
                                    stamp = probe['Start']
                                    records.append({'id': hashlib.sha256(f'{cid}:health:{stamp}'.encode()).hexdigest(),
                                                    'timestamp': stamp, 'text': f'{stamp} WARN Container health check failed: ' + redact(probe.get('Output', ''))[:1000]})
                            if not state.get('Running') and (state.get('ExitCode', 0) != 0 or state.get('OOMKilled')):
                                stamp = state.get('FinishedAt', container['Created'])
                                text = f"{stamp} FATAL Container terminated: exitCode={state.get('ExitCode')} OOMKilled={state.get('OOMKilled', False)}"
                                records.append({'id': hashlib.sha256(f'{cid}:exit:{stamp}'.encode()).hexdigest(), 'timestamp': stamp, 'text': text})
                                last = max(last, stamp)
                            await send()  # Empty batches flush multiline buffers and refresh heartbeat.
                        finally:
                            if proc.returncode is None:
                                proc.kill()
                                await proc.wait()
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Do not print logs, provider responses, credentials or command environments.
                print(f'Collector reconnecting: {type(exc).__name__}', flush=True)
                await asyncio.sleep(3)


if __name__ == '__main__':
    asyncio.run(main())
