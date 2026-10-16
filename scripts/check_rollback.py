"""Real, isolated rollback proof; never approves or deploys a Fix Agent patch.

Run with .venv/bin/python scripts/check_rollback.py. Only DockerDeployment.build
is replaced: it builds an actual image whose Java entrypoint names a missing jar.
Tests, image activation, health checks, previous-image preservation, journal, and
rollback are the production Runner operations. This proves operational rollback,
not that a Java fix was built correctly or that database migrations are reversible.

The fixed project day35-doctor-rollback publishes localhost:8086 and owns its own
volumes. Cleanup uses down WITHOUT -v; test worktrees, images, volumes and redacted
evidence remain for audit. Every run creates new worktrees; existing containers
cause a safe refusal, and refreshed runtime config copies are archived first.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

from doctor.config import get_settings
from doctor.runner import DockerDeployment, Runner, run_command
from doctor.security import redact_data


PROJECT = 'day35-doctor-rollback'
SOURCE = Path(get_settings().source_repo)
BASE_URL = 'http://127.0.0.1:8086'
IMAGE_ID = re.compile(r'sha256:[a-f0-9]{64}')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(argv, timeout=60):
    result = run_command(argv, timeout=timeout)
    # Do not propagate raw Docker/Compose output: it may contain environment data.
    require(result['returncode'] == 0 and not result.get('timed_out') and
            not result.get('truncated'), 'External command failed or exceeded its bound')
    return result['output'].strip()


def private_directory(path):
    require(not path.is_symlink(), 'Refusing a symlinked runtime directory')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(path.is_dir(), 'Runtime directory is not a directory')


def create_or_verify(path, content):
    require(not path.is_symlink(), 'Refusing a symlinked runtime file')
    if path.exists():
        require(path.is_file() and path.read_bytes() == content,
                'Existing runtime configuration differs; preserve it and review manually')
        os.chmod(path, 0o600)
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)


def refresh_runtime_copy(path, content, archive):
    """Preserve previous generated config before copying the new target version."""
    require(not path.is_symlink(), 'Refusing a symlinked runtime file')
    if not path.exists() or path.read_bytes() == content:
        create_or_verify(path, content)
        return
    require(path.is_file(), 'Runtime configuration is not a regular file')
    private_directory(archive)
    create_or_verify(archive / path.name, path.read_bytes())
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.rollback-config-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_evidence(path, data):
    require(not path.is_symlink(), 'Refusing a symlinked evidence file')
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.rollback-evidence-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(redact_data(data), stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class BrokenImageDeployment(DockerDeployment):
    """Fault injection is confined to building a real broken Docker image."""

    def build(self, context, patch_sha):
        fixture = context / 'rollback-fault-injection'
        fixture.mkdir()
        (fixture / 'Dockerfile').write_text(
            f'FROM {self.baseline_image}\n'
            'ENTRYPOINT ["java", "-jar", "/app/nonexistent-doctor-rollback-test.jar"]\n')
        tag = f'{PROJECT}-candidate:{self.test_id}'
        self.command(['docker', 'build', '--network', 'none', '--pull=false',
                      '--label', 'doctor.patch=' + patch_sha,
                      '--label', 'org.opencontainers.image.revision=' + self.runner.head(),
                      '--label', 'doctor.rollback-test=' + self.test_id,
                      '--tag', tag, str(fixture)], timeout=120)
        image = self.command(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag]).strip()
        require(IMAGE_ID.fullmatch(image), 'Invalid candidate image id')
        require(image != self.baseline_image, 'Candidate must differ from the baseline')
        revision = self.command(['docker', 'image', 'inspect', '--format',
                                 '{{index .Config.Labels "org.opencontainers.image.revision"}}', image]).strip()
        require(revision == self.runner.head(), 'Candidate lost the baseline source provenance')
        return image


def run_proof(args):
    runtime = ROOT / '.runtime'
    private_directory(runtime)
    directory = runtime / 'rollback'
    private_directory(directory)
    lock_path = directory / '.check.lock'
    require(not lock_path.is_symlink(), 'Refusing a symlinked test lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return isolated_proof(args, directory, runtime)


def isolated_proof(args, directory, runtime):
    test_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
    run_dir = directory / ('run-' + test_id)
    run_dir.mkdir(mode=0o700)
    evidence = {
        'test_id': test_id, 'project': PROJECT, 'status': 'FAILED',
        'images': {}, 'health': {}, 'testresult': {'passed': False},
        'fault_injection': 'Only DockerDeployment.build: real image with missing-jar entrypoint',
        'limits': ['Not a real Fix Agent patch approval', 'Does not validate Java image construction',
                   'No database migration rollback is attempted'],
        'cleanup': {'down_without_volumes': False},
    }
    phase = 'preflight'
    launch_attempted = False
    compose = directory / 'compose.yaml'
    override = run_dir / 'baseline-image.json'
    compose_args = ['docker', 'compose', '--project-name', PROJECT, '--file', str(compose),
                    '--file', str(override)]
    try:
        require(not command(['docker', 'ps', '-aq', '--filter',
                             f'label=com.docker.compose.project={PROJECT}']),
                'Rollback project already has containers; refusing to alter them')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 8086))

        # Capture the current immutable release before worktree creation or tests;
        # later live deployments cannot retarget this isolated baseline.
        baseline = args.baseline_image or command([
            'docker', 'inspect', '--format', '{{.Image}}', 'day35-aurelspa-spring-1'])
        labels = json.loads(command(['docker', 'image', 'inspect', '--format', '{{json .Config.Labels}}', baseline])) or {}
        if labels.get('doctor.patch') and not args.baseline_image:
            journal = Path(get_settings().workspace_root) / '.runner-state/deployment.json'
            recorded = json.loads(journal.read_text()) if journal.exists() else {}
            require(recorded.get('status') == 'SUCCEEDED' and recorded.get('image') == baseline,
                    'Patched running image requires its verified deployment journal')
            baseline = recorded['previous_image']
            labels = json.loads(command(['docker', 'image', 'inspect', '--format', '{{json .Config.Labels}}', baseline])) or {}
        require(not labels.get('doctor.patch'), 'Select an unpatched provenance-labelled baseline image for this isolated proof')
        require(IMAGE_ID.fullmatch(baseline), 'Baseline must be an immutable Docker image id')
        require(command(['docker', 'image', 'inspect', '--format', '{{.Id}}', baseline]) == baseline,
                'Baseline image is not available locally')
        evidence['images']['baseline'] = baseline
        revision = labels.get('org.opencontainers.image.revision', '')
        require(re.fullmatch('[a-f0-9]{40,64}', revision), 'Baseline has no immutable source provenance')
        require(command(['git', '-C', str(SOURCE), 'rev-parse', '--verify', revision + '^{commit}']) == revision,
                'Exact baseline source commit is unavailable')
        evidence['base_commit'] = revision
        baseline_revision = command(['docker', 'image', 'inspect', '--format',
                                     '{{index .Config.Labels "org.opencontainers.image.revision"}}', baseline])
        require(baseline_revision == revision, 'Baseline provenance does not match integration HEAD')
        evidence['baseline_source_revision'] = baseline_revision

        # Copy RAW compose, changing only the project and public HTTP port. A
        # resolved compose dump would copy live-volume names and disclose secrets.
        raw = (SOURCE / 'compose.yaml').read_text()
        require(raw.count('name: day35-aurelspa\n') == 1 and
                raw.count('127.0.0.1:8085:8080') == 1, 'Unexpected integration compose layout')
        raw = raw.replace('name: day35-aurelspa\n', f'name: {PROJECT}\n', 1)
        raw = raw.replace('127.0.0.1:8085:8080', '127.0.0.1:8086:8080', 1)
        refresh_runtime_copy(compose, raw.encode(), run_dir / 'previous-config')
        private_env = (SOURCE / '.env').read_text()
        private_env = re.sub(r'^SOURCE_REVISION=.*\n?', '', private_env, flags=re.M).rstrip() + '\nSOURCE_REVISION=' + revision + '\n'
        refresh_runtime_copy(directory / '.env', private_env.encode(), run_dir / 'previous-config')
        os.environ['SOURCE_REVISION'] = revision  # This test process only; never changes target .env.
        env = dotenv_values(directory / '.env')
        for key in ('MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD', 'DOCTOR_DEMO_TOKEN', 'SOURCE_REVISION'):
            require(bool(env.get(key)), 'Required isolated environment value is missing')
            require(key not in os.environ or os.environ[key] == env[key],
                    'Inherited environment would override the isolated compose configuration')
        require(env['SOURCE_REVISION'] == revision, 'Target environment provenance does not match integration HEAD')
        create_or_verify(override, json.dumps({'services': {'spring': {'image': baseline}}}).encode())

        # Resolve privately only to check isolation, NEVER save or print this data.
        config = json.loads(command(compose_args + ['config', '--format', 'json']))
        require(config['name'] == PROJECT and set(config['services']) == {'mysql', 'spring'},
                'Unexpected isolated project or services')
        for kind, expected in (('volumes', {'mysql', 'uploads'}),
                               ('networks', {'internal', 'frontend'})):
            require(set(config[kind]) == expected, 'Unexpected isolated resources')
            for key, value in config[kind].items():
                require(not value.get('external') and value.get('name') == f'{PROJECT}_{key}',
                        'Compose would reuse resources outside the rollback project')
        require(config['services']['spring']['image'] == baseline, 'Baseline override was not applied')
        require(not config['services']['mysql'].get('ports'), 'MySQL must not publish a host port')
        ports = config['services']['spring'].get('ports', [])
        require(len(ports) == 1 and ports[0]['host_ip'] == '127.0.0.1' and
                str(ports[0]['published']) == '8086' and ports[0]['target'] == 8080,
                'Unexpected isolated HTTP publication')
        for service, expected in (('mysql', {'mysql'}), ('spring', {'uploads'})):
            mounts = config['services'][service].get('volumes', [])
            require({m.get('source') for m in mounts} == expected and
                    all(m.get('type') == 'volume' for m in mounts), 'Unexpected shared service mounts')
        del config, env

        phase = 'clean_source_worktree'
        source = run_dir / 'source'
        command(['git', '-C', str(SOURCE), 'worktree', 'add', '--detach', str(source), revision])
        settings = get_settings().model_copy(update={
            'source_repo': str(source), 'workspace_root': str(run_dir / 'workspaces'),
            'compose_project': PROJECT, 'allowed_services': 'spring',
            'target_compose_file': str(compose),
            'target_health_url': BASE_URL + '/health', 'target_smoke_url': BASE_URL + '/',
            'target_demo_url': BASE_URL + '/doctor-demo/normalize',
            'demo_token': dotenv_values(directory / '.env')['DOCTOR_DEMO_TOKEN'],
            'runner_token': secrets.token_hex(32), 'deployment_health_timeout_seconds': 30,
        })
        runner = Runner(settings=settings)
        ops = BrokenImageDeployment(runner)
        ops.baseline_image, ops.test_id = baseline, test_id
        runner.ops = ops
        evidence['source_worktree'] = str(source)
        evidence['runner_workspace_root'] = str(runner.root)

        phase = 'baseline_startup'
        launch_attempted = True
        command(compose_args + ['up', '-d', '--no-build', '--pull', 'never', '--wait',
                                '--wait-timeout', '120', 'mysql', 'spring'], timeout=240)
        before = ops.current()
        evidence['health']['baseline'] = ops.probe(settings.target_health_url)
        require(before['image'] == baseline and before['docker_health'] == 'healthy' and
                evidence['health']['baseline']['ok'], 'Isolated baseline is not healthy')
        require(before.get('source_revision') == revision, 'Running baseline lost its source provenance')

        phase = 'trusted_tests'
        job_id = str(10**16 + secrets.randbelow(9 * 10**16))
        workspace = runner.workspace({'job_id': job_id, 'base_commit': revision,
                                      'service': 'spring', 'version': baseline})
        workspace_id = workspace['workspace_id']
        filename = 'src/main/java/com/example/InternetStore/doctorDemo/DemoNameNormalizer.java'
        package_line = 'package com.example.InternetStore.doctorDemo;\n'
        runner.tool({'workspace_id': workspace_id, 'action': 'file_apply_patch', 'path': filename,
                     'replacements': [{'old': package_line, 'new': package_line + '\n'}]})
        diff = runner.tool({'workspace_id': workspace_id, 'action': 'git_diff'})
        require(diff['changed_files'] == [filename] and diff['patch'], 'Unexpected test patch scope')
        tests = runner.tool({'workspace_id': workspace_id, 'action': 'run_allowed_tests'})
        evidence['testresult'] = {key: tests[key] for key in
                                  ('passed', 'timed_out', 'selected_tests', 'java_version')}
        evidence['patch_sha'] = diff['patch_sha']
        evidence['workspace_id'] = workspace_id
        evidence['incident_version'] = baseline
        require(tests['passed'] and tests['patch_sha'] == diff['patch_sha'],
                'The exact harmless patch did not pass real trusted Maven tests')

        phase = 'confirmed_isolated_fault_injection'
        # User authorization applies ONLY to this fault-injection project/hash;
        # it is NOT approval for any live Fix Agent patch or main project deploy.
        result = runner.deploy({'workspace_id': workspace_id, 'confirmed': True,
                                'patch_sha': diff['patch_sha']})
        evidence['deployment'] = {key: result[key] for key in
                                  ('status', 'base_commit', 'patch_sha', 'previous_image', 'image',
                                   'started_at', 'baseline', 'rollback') if key in result}
        evidence['images']['candidate'] = result.get('image')
        evidence['rollback_cause'] = result.get('error', '')
        restored = ops.current()
        evidence['images']['restored'] = restored['image']
        evidence['health']['restored'] = ops.probe(settings.target_health_url)
        evidence['health']['smoke'] = ops.probe(settings.target_smoke_url)
        require(result['status'] == 'ROLLED_BACK', 'Runner did not complete a real rollback')
        require(result['previous_image'] == baseline == restored['image'],
                'Rollback did not restore the exact previous image id')
        require(restored.get('source_revision') == revision, 'Rollback lost the previous source provenance')
        require(restored['docker_health'] == 'healthy' and
                evidence['health']['restored'].get('status') == 200 and
                evidence['health']['smoke'].get('status') == 200,
                'Restored release failed real HTTP/Docker health verification')

        phase = 'docker_event_evidence'
        events = command(['docker', 'events', '--since', result['started_at'],
                          '--until', datetime.now(timezone.utc).isoformat(),
                          '--filter', f'label=com.docker.compose.project={PROJECT}',
                          '--filter', 'label=com.docker.compose.service=spring',
                          '--format', '{{.TimeNano}} {{.Action}} {{.Actor.ID}} '
                          '{{index .Actor.Attributes "image"}}'])
        selected = []
        for line in events.splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[1] in {'create', 'start', 'die', 'destroy'}:
                selected.append({'timestamp_ns': parts[0], 'action': parts[1],
                                 'container_id': parts[2], 'image': parts[3]})
        evidence['docker_events'] = selected
        require(any(event['action'] == 'start' and event['image'] == result['image']
                    for event in selected), 'No real candidate start event was observed')
        require(any(event['action'] == 'start' and event['image'] == baseline
                    for event in selected), 'No real rollback image start event was observed')
        evidence['status'] = 'ROLLED_BACK'
    except (Exception, KeyboardInterrupt) as exc:
        # Do not persist exception text from subprocesses or private config parsing.
        evidence['failure'] = {'phase': phase, 'type': type(exc).__name__}
        if getattr(exc, 'detail', '') == (
                'Tracked credential must be moved to environment configuration before sandbox execution'):
            evidence['failure']['reason'] = 'Runner rejected a credential-like tracked configuration value'
        evidence['status'] = 'FAILED'
    finally:
        if launch_attempted:
            try:
                command(compose_args + ['down', '--timeout', '20'], timeout=90)
                remaining = command(['docker', 'ps', '-aq', '--filter',
                                     f'label=com.docker.compose.project={PROJECT}'])
                require(not remaining, 'Rollback project containers remain after cleanup')
                evidence['cleanup']['down_without_volumes'] = True
            except (Exception, KeyboardInterrupt) as exc:
                evidence['cleanup']['error_type'] = type(exc).__name__
                evidence['status'] = 'FAILED'
        evidence['finished_at'] = datetime.now(timezone.utc).isoformat()
        save_evidence(run_dir / 'evidence.json', evidence)
        # Preserve earlier blocked evidence until an actual complete proof exists.
        if evidence['status'] == 'ROLLED_BACK':
            save_evidence(runtime / 'rollback-evidence.json', evidence)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-image', help='Immutable sha256 image id captured before a live deployment')
    args = parser.parse_args()
    try:
        evidence = run_proof(args)
        summary = {key: evidence[key] for key in ('project', 'status', 'images', 'health', 'testresult')}
    except (Exception, KeyboardInterrupt):
        summary = {'project': PROJECT, 'status': 'REFUSED', 'images': {}, 'health': {},
                   'testresult': {'passed': False}}
    print(json.dumps(summary, indent=2))
    return 0 if summary['status'] == 'ROLLED_BACK' else 1


if __name__ == '__main__':
    sys.exit(main())
