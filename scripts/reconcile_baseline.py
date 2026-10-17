"""Host-operator-only adoption of an explicitly named, source-verified baseline.

Never exposed as an API/model tool. Rebuilds clean source and compares every JAR
entry. Retains the running image, or explicitly replaces it with equivalent
bytecode and corrected source provenance. Archives journals, not workspaces.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from doctor.runner import Runner


TERMINAL = {'SUCCEEDED', 'ROLLED_BACK', 'ABORTED'}


def jar_manifest(path):
    """Ignore ZIP timestamps, not content; duplicate entries are ambiguous."""
    manifest = {}
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            if entry.filename in manifest:
                raise RuntimeError('Duplicate JAR entries cannot be verified')
            manifest[entry.filename] = hashlib.sha256(archive.read(entry)).hexdigest()
    if not any(name.startswith('BOOT-INF/classes/') for name in manifest):
        raise RuntimeError('Expected a Spring Boot application JAR')
    return manifest


def validate_baseline(runner, expected_image, *, replace_equivalent=False):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', expected_image):
        raise RuntimeError('An immutable expected image ID is required')
    journal = runner.load('deployment.json')
    if journal and journal.get('status') not in TERMINAL:
        raise RuntimeError('Unfinished deployment: reconcile it first')
    revision = runner.head()
    if runner.git(runner.source, 'status', '--porcelain', '--untracked-files=all').strip():
        raise RuntimeError('Source must be clean')
    current = runner.ops.current()
    if current['image'] != expected_image:
        raise RuntimeError('Running image differs from explicit operator approval')
    if current.get('patch_sha') or not re.fullmatch(r'[a-f0-9]{40,64}', current.get('source_revision', '')):
        raise RuntimeError('Only an unpatched baseline at the current source HEAD may be adopted')
    if current['source_revision'] != revision:
        if not replace_equivalent:
            raise RuntimeError('Only an unpatched baseline at the current source HEAD may be adopted')
        runner.git(runner.source, 'merge-base', '--is-ancestor', current['source_revision'], revision)
    if not current.get('running') or current.get('docker_health') != 'healthy':
        raise RuntimeError('Running container must be healthy')
    return revision, current


def require_api_idle(runner):
    # Read the real container DB, never the unrelated host SQLite database.
    project = runner.settings.compose_project
    ids = runner.ops.command(['docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={project}',
                              '--filter', 'label=com.docker.compose.service=api']).split()
    if len(ids) != 1:
        raise RuntimeError('Expected exactly one API container for the configured project')
    code = '''
from sqlalchemy import select
from doctor.db import Session, FixJob, Deployment, State
with Session() as s:
    busy = s.scalar(select(FixJob.id).where(FixJob.status.in_(['QUEUED','RUNNING','PREPARING','TESTING'])).limit(1))
    busy = busy or s.scalar(select(Deployment.id).where(Deployment.status.in_(['QUEUED','RUNNING','DEPLOYING','UNKNOWN','INTERRUPTED','ROLLBACK_FAILED'])).limit(1))
    busy = busy or any(row.value.get('status') in {'RUNNING','UNKNOWN'} for row in s.scalars(select(State).where(State.key.like('manual-git:%'))))
    print('BUSY' if busy else 'IDLE')
'''
    if runner.ops.command(['docker', 'exec', ids[0], 'python', '-c', code]).strip() != 'IDLE':
        raise RuntimeError('Active or unsettled API work: stop and inspect it first')


def reconcile(runner, expected_image, *, confirmed=False, replace_equivalent=False):
    if confirmed is not True:
        raise RuntimeError('Explicit --confirmed operator approval is required')
    with runner.locked():
        revision, current = validate_baseline(runner, expected_image, replace_equivalent=replace_equivalent)
        require_api_idle(runner)
        tag = 'doctor-baseline-verify:' + uuid.uuid4().hex
        with runner.snapshot(runner.source) as context:
            print('Building clean source and running its Dockerfile tests...', flush=True)
            runner.ops.command(['docker', 'build', '--build-arg', 'SOURCE_REVISION=' + revision,
                                '--tag', tag, '--file', str(context / 'Dockerfile'), str(context)], timeout=600)
        built = runner.ops.command(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag]).strip()
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', built):
            raise RuntimeError('Invalid verification image ID')
        with tempfile.TemporaryDirectory(prefix='baseline-verify-', dir=runner.state) as directory:
            root = Path(directory)
            temporary_container = runner.ops.command(['docker', 'create', '--network', 'none',
                                                       '--entrypoint', '/bin/true', built]).strip()
            if not re.fullmatch(r'[a-f0-9]{64}', temporary_container):
                raise RuntimeError('Invalid temporary container ID')
            try:
                runner.ops.command(['docker', 'cp', current['container_id'] + ':/app/app.jar', str(root / 'running.jar')])
                runner.ops.command(['docker', 'cp', temporary_container + ':/app/app.jar', str(root / 'built.jar')])
                running_manifest = jar_manifest(root / 'running.jar')
                built_manifest = jar_manifest(root / 'built.jar')
            finally:
                runner.ops.command(['docker', 'rm', temporary_container])
        if running_manifest != built_manifest:
            changed = sorted(name for name in running_manifest.keys() | built_manifest.keys()
                             if running_manifest.get(name) != built_manifest.get(name))
            raise RuntimeError('Running JAR differs from clean source: ' + ', '.join(changed[:12]))
        print(f'All {len(running_manifest)} JAR entries match clean source.', flush=True)
        verification = runner.ops.verify({}, datetime.now(timezone.utc).isoformat(), rollback=True)
        demo = runner.ops.probe(runner.settings.target_demo_url, demo=True)
        if not verification.get('ok') or not demo.get('ok'):
            raise RuntimeError('Health/site/demo verification failed')
        # Recheck after the long build, before changing any trusted state.
        require_api_idle(runner)
        final_revision, final = validate_baseline(runner, expected_image, replace_equivalent=replace_equivalent)
        if final_revision != revision or final['container_id'] != current['container_id']:
            raise RuntimeError('Source or container changed during verification')
        stamp = datetime.now(timezone.utc).isoformat()
        audit_name = 'baseline-adoption-' + uuid.uuid4().hex + '.json'
        audit = {'status': 'VALIDATED', 'operation': 'BASELINE_ADOPTION', 'confirmed': True,
                 'created_at': stamp, 'image': built if replace_equivalent else expected_image, 'base_commit': revision,
                 'previous_image': expected_image, 'replace_equivalent': replace_equivalent,
                 'verification_image': built, 'jar_entries': len(running_manifest),
                 'jar_manifest_sha256': hashlib.sha256(json.dumps(running_manifest, sort_keys=True).encode()).hexdigest(),
                 'verification': verification, 'demo': demo,
                 'previous_active': runner.load('active.json'), 'previous_deployment': runner.load('deployment.json')}
        runner.save(audit_name, audit)
        journal = {'status': 'PREPARED', 'operation': 'BASELINE_ADOPTION',
                   'image': audit['image'], 'base_commit': revision, 'started_at': stamp,
                   'previous_image': expected_image, 'previous_active': audit['previous_active'],
                   'audit': audit_name, 'verification': verification}
        if replace_equivalent:
            runner.save('deployment.json', journal)
            try:
                journal['status'] = 'SWITCHING'
                runner.save('deployment.json', journal)
                runner.ops.activate(built)
                journal['status'] = 'VERIFYING'
                runner.save('deployment.json', journal)
                verification = runner.ops.verify({}, stamp, rollback=True)
                demo = runner.ops.probe(runner.settings.target_demo_url, demo=True)
                if runner.ops.current()['image'] != built or not verification.get('ok') or not demo.get('ok'):
                    raise RuntimeError('Equivalent baseline activation failed verification')
                journal['verification'] = verification
            except Exception:
                recovery = runner.rollback(journal)
                runner.save(audit_name, {**audit, 'status': recovery['status']})
                raise
        # A baseline has no AI patch. Keep its complete prior journal in the
        # durable audit rather than inventing an approved patch for this image.
        runner.save('active.json', None)
        runner.save('deployment.json', {**journal, 'status': 'SUCCEEDED'})
        runner.save(audit_name, {**audit, 'status': 'SUCCEEDED'})
        return {'status': 'SUCCEEDED', 'image': audit['image'], 'audit': audit_name,
                'verified_entries': len(running_manifest)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-image', required=True)
    parser.add_argument('--confirmed', action='store_true')
    parser.add_argument('--source-repo', help='Explicit clean source worktree; original checkout is not changed')
    parser.add_argument('--replace-equivalent-image', action='store_true',
                        help='Activate identical application bytecode with the source worktree revision label')
    args = parser.parse_args()
    from doctor.config import get_settings
    settings = get_settings()
    if args.source_repo:
        settings = settings.model_copy(update={'source_repo': str(Path(args.source_repo).resolve())})
    print(json.dumps(reconcile(Runner(settings), args.expected_image, confirmed=args.confirmed,
                               replace_equivalent=args.replace_equivalent_image)))


if __name__ == '__main__':
    main()
