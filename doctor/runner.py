"""Trusted host runner. Run with uvicorn doctor.runner:app --uds ... .

Only this process needs Docker access. Its token and state directory must not be
mounted into the API or into any build/test container.
"""
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException

from .config import get_settings
from .security import redact, redact_data


OUTPUT_LIMIT = 24000
FILE_LIMIT = 1024 * 1024
PATCH_LIMIT = 512 * 1024
JAVA_ROOTS = ('src/main/java/', 'src/test/java/')
RESOURCE_ROOTS = ('src/main/resources/templates/', 'src/main/resources/static/')
_THREAD_LOCK = threading.RLock()


def fail(message, code=400):
    raise HTTPException(code, message)


def bounded(value, limit=OUTPUT_LIMIT):
    return str(value)[:limit]


def run_command(argv, *, cwd=None, timeout=60, output_limit=OUTPUT_LIMIT, env=None):
    """No shell; drain output without retaining unbounded subprocess output."""
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    output = bytearray()
    truncated = False
    marker = b'\n... output truncated; final diagnostics follow ...\n'
    head_size = output_limit // 4
    deadline = time.monotonic() + timeout
    timed_out = False
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            if time.monotonic() >= deadline:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                break
            for key, _ in selector.select(min(.2, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(chunk)
                    if len(output) > output_limit:
                        # Maven's failures and assertion summary are at the END.
                        # Drain all output, retaining a bounded head and tail.
                        truncated = True
                        del output[head_size:len(output) - (output_limit - head_size)]
    # A child may close stdout before exiting: the same deadline still applies.
    try:
        process.wait(timeout=max(.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    process.stdout.close()
    if truncated and output_limit > len(marker):
        output[head_size:head_size + len(marker)] = marker
    return {'returncode': process.returncode, 'output': output.decode('utf-8', 'replace'),
            'timed_out': timed_out, 'truncated': truncated}


def _secret_path(path):
    parts = PurePosixPath(path).parts
    return any(part.lower() in {'.git', '.ssh', '.aws', '.gnupg', 'secrets', 'private', 'credentials', 'credentials.json'}
               or part.lower().startswith('.env')
               or part.lower().endswith(('.pem', '.key', '.p12', '.pfx', '.jks', '.keystore'))
               or part.lower().startswith(('id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa'))
               or re.search(r'private[-_]?key', part, re.I) for part in parts)


def _private_content(content):
    return bool(re.search(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----', content))


def _embedded_credential(content):
    """Fail closed before executing generated Java against a build snapshot."""
    if re.search(rb'\bsk-[A-Za-z0-9_-]{16,}\b', content):
        return True
    for line in content.decode('utf-8', 'ignore').splitlines():
        match = re.match(r'\s*[\w.\-]*(?:password|passwd|api[-_]?key|access[-_]?token|client[-_]?secret)\s*[=:]\s*(.*)', line, re.I)
        if match:
            value = match.group(1).strip().strip('\"\'')
            if value and not value.startswith('${'):
                return True
    return False


def safe_path(root, name, *, java=False):
    if (not isinstance(name, str) or not name or len(name) > 400 or '\\' in name or
            any(ord(c) < 32 for c in name)):
        fail('Invalid relative filename')
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in {'.', '..'} for p in name.split('/')) or _secret_path(name):
        fail('Forbidden filename')
    if java and not ((name.startswith(JAVA_ROOTS) and name.endswith('.java')) or
                     (name.startswith(RESOURCE_ROOTS) and name.endswith(('.html', '.css')))):
        fail('Only Java source/tests and HTML/CSS application resources may be edited')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            fail('Symlinks are forbidden')
    if not current.resolve().is_relative_to(root.resolve()):
        fail('Filename escapes repository')
    return current


class Runner:
    def __init__(self, settings=None, command=run_command, deployment_ops=None):
        self.settings = settings or get_settings()
        self.source = Path(self.settings.source_repo).resolve()
        self.root = Path(self.settings.workspace_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = self.root / '.runner-state'
        if self.state.is_symlink():
            fail('Unsafe runner state directory')
        self.state.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.state, 0o700)
        self.command = command
        self.ops = deployment_ops or DockerDeployment(self)

    @contextlib.contextmanager
    def locked(self):
        with _THREAD_LOCK, (self.state / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def save(self, name, data):
        fd, temporary = tempfile.mkstemp(dir=self.state)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state / name)
            directory = os.open(self.state, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, name):
        path = self.state / name
        return json.loads(path.read_text()) if path.exists() else None

    def git(self, root, *args, timeout=30, limit=OUTPUT_LIMIT):
        # The command runner caps normal output. Diffs/manifests need a larger,
        # explicitly bounded limit, obtained through git's own output to a file.
        if limit > OUTPUT_LIMIT:
            with tempfile.NamedTemporaryFile(dir=self.state) as target:
                result = self.command(['git', '-C', str(root), args[0], '--output=' + target.name, *args[1:]], timeout=timeout)
                if result['returncode']:
                    fail('Git command failed: ' + bounded(result['output']), 409)
                if Path(target.name).stat().st_size > limit:
                    fail('Patch exceeds runner limit', 409)
                return Path(target.name).read_text()
        result = self.command(['git', '-C', str(root), *args], timeout=timeout)
        if result['returncode'] or result.get('truncated'):
            fail('Git command failed or output exceeds limit: ' + bounded(result['output']), 409)
        return result['output']

    def head(self):
        revision = self.git(self.source, 'rev-parse', '--verify', 'HEAD').strip()
        if not re.fullmatch('[0-9a-f]{40,64}', revision):
            fail('Invalid repository revision', 409)
        return revision

    def source_files(self, root=None):
        result = self.command(['git', '-C', str(root or self.source), 'ls-files', '-z'],
                              timeout=30, output_limit=512 * 1024)
        if result['returncode'] or result.get('truncated'):
            fail('Tracked file manifest exceeds the bounded runner limit', 409)
        return [p for p in result['output'].split('\0') if p and not _secret_path(p)]

    def source_action(self, data):
        action = data.get('action')
        if action == 'revision':
            revision = self.head()
            return {'revision': revision, 'base_commit': revision}
        files = self.source_files()
        if action == 'read':
            name = data.get('path', '')
            if name not in files:
                fail('File is not a readable tracked source file', 404)
            return self.read_file(self.source, data)
        if action == 'search':
            return self.search(self.source, files, data)
        if action == 'context':
            event = data.get('event') or {}
            if not isinstance(event, dict):
                fail('Event must be an object')
            version = event.get('version', '')
            revision, context_root = self.head(), self.source
            if version:
                active = self.load('active.json') or {}
                active_version = active.get('base_commit', '') + ':' + active.get('patch_sha', '')
                if version == active_version and active.get('workspace_id'):
                    context_root, metadata = self.workspace_meta(active['workspace_id'])
                    if self.diff(context_root, metadata)['patch_sha'] != active['patch_sha']:
                        fail('Deployed source workspace changed; source context cannot be verified', 409)
                    files = self.source_files(context_root)
                    revision = active_version
                elif re.fullmatch('[0-9a-f]{40,64}', version):
                    revision = version
                else:
                    return {'files': [], 'reason': 'No verifiable source revision for this historical image'}
            frames = data.get('frames', event.get('application_frames', event.get('frames', [])))
            frames = frames[:30] if isinstance(frames, list) else frames
            if not isinstance(frames, list) or len(frames) > 30:
                fail('At most 30 stack frames are allowed')
            result, context_size = [], 0
            for frame in frames:
                if not isinstance(frame, dict):
                    continue
                filename = frame.get('file') or frame.get('filename', '')
                class_name = frame.get('class') or frame.get('class_name', '')
                if class_name:
                    filename = class_name.split('$')[0].replace('.', '/') + '.java'
                matches = [p for p in files if p.endswith('.java') and
                           (p == filename or p.endswith('/' + filename))] if filename else []
                line = frame.get('line', frame.get('line_number', 0))
                if len(matches) != 1 or not isinstance(line, int) or line < 1:
                    continue
                arguments = {'path': matches[0], 'start_line': max(1, line - 5), 'end_line': line + 5}
                if context_root == self.source and revision != self.head():
                    result_at = self.command(['git', '-C', str(self.source), 'show', revision + ':' + matches[0]],
                                             timeout=20, output_limit=FILE_LIMIT)
                    if result_at['returncode'] or result_at.get('truncated') or _private_content(result_at['output'].encode()):
                        continue
                    lines = result_at['output'].splitlines()
                    start, end = arguments['start_line'], arguments['end_line']
                    snippet = {'path': matches[0], 'start_line': start, 'total_lines': len(lines),
                               'content': '\n'.join(f'{index + start}: {value}' for index, value in enumerate(lines[start - 1:end]))}
                else:
                    snippet = self.read_file(context_root, arguments)
                snippet['content'] = bounded(snippet['content'], 6000)
                context_size += len(snippet['content'])
                if context_size > 18000:
                    break
                result.append({'path': matches[0], 'line': line, **snippet})
            return {'frames': [{'path': item['path'], 'line': item['line']} for item in result], 'files': result, 'source_revision': revision}
        fail('Unknown source action')

    def read_file(self, root, data):
        path = safe_path(root, data.get('path', ''))
        if not path.is_file() or path.stat().st_size > FILE_LIMIT:
            fail('File missing or too large', 404)
        if _private_content(path.read_bytes()):
            fail('Private key material is not readable', 403)
        start = data.get('start_line', 1)
        end = data.get('end_line', start + 79 if type(start) is int else 80)
        if type(start) is not int or type(end) is not int or start < 1 or end < start or end - start > 119:
            fail('Invalid line range (maximum 120 lines)')
        try:
            lines = path.read_text().splitlines()
        except UnicodeError:
            fail('Only text files are readable')
        return {'path': data['path'], 'start_line': start, 'total_lines': len(lines),
                'content': bounded('\n'.join(f'{i + start}: {v}' for i, v in enumerate(lines[start - 1:end])))}

    def search(self, root, files, data):
        query = data.get('query', '')
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            fail('Search requires 1–200 literal characters')
        scope = data.get('path', '')
        safe_path(root, scope)
        files = [name for name in files if name == scope or name.startswith(scope.rstrip('/') + '/')]
        if len(files) > 50:
            fail('Уточните путь поиска: допускается не более 50 релевантных файлов')
        matches, size = [], 0
        for name in files:
            path = safe_path(root, name)
            if not path.is_file() or path.stat().st_size > FILE_LIMIT:
                continue
            if _private_content(path.read_bytes()):
                continue
            try:
                lines = path.read_text().splitlines()
            except UnicodeError:
                continue
            for line, text in enumerate(lines, 1):
                if query in text:
                    item = {'path': name, 'line': line, 'text': text[:300]}
                    matches.append(item)
                    size += len(json.dumps(item))
                    if len(matches) >= 50 or size > 18000:
                        return {'matches': matches, 'truncated': True}
        return {'matches': matches, 'truncated': False}

    def list_files(self, root, data):
        """Filename discovery only: no broad content scan or secret filenames."""
        scope = data.get('path', '')
        safe_path(root, scope)
        if not scope.startswith('src/') and scope not in {'src', 'pom.xml'}:
            fail('Forbidden filename')
        files = [name for name in self.source_files(root)
                 if name == scope or name.startswith(scope.rstrip('/') + '/')]
        return {'files': files[:120], 'truncated': len(files) > 120}

    def workspace(self, data):
        job_id = str(data.get('job_id', ''))
        if not re.fullmatch('[1-9][0-9]{0,17}', job_id):
            fail('A positive numeric job_id is required')
        if (data.get('service') and getattr(self.ops, 'service', None) and
                data['service'] != self.ops.service):
            fail('Incident does not belong to the configured deployment service', 409)
        workspace_id = 'job-' + job_id
        with self.locked():
            existing = self.load(workspace_id + '.json')
            if existing:
                path, existing = self.workspace_meta(workspace_id)
                if existing['base_commit'] != self.head():
                    fail('Stale workspace base commit', 409)
                self.inherit_active_release(path, existing)
                return {'workspace_id': workspace_id, 'base_commit': existing['base_commit'],
                        'inherited_patch_sha': existing.get('parent_patch_sha', '')}
            base = self.head()
            if data.get('base_commit') not in (None, base):
                fail('Requested revision is not the current HEAD', 409)
            path = self.root / workspace_id
            if path.exists() or path.is_symlink():
                fail('Workspace path already exists without trusted metadata', 409)
            self.git(self.source, 'worktree', 'add', '--detach', str(path), base)
            try:
                base_image = self.ops.current()['image']
            except Exception:
                base_image = ''  # Diagnosis/fix preparation remain possible when target is down.
            metadata = {'workspace_id': workspace_id, 'base_commit': base,
                        'base_image': base_image,
                        'exception_type': bounded(data.get('exception_type', ''), 256),
                        'fingerprint': bounded(data.get('fingerprint', ''), 128),
                        'incident_service': bounded(data.get('service', ''), 100),
                         'incident_version': bounded(data.get('version', ''), 512), 'tests': None}
            self.save(workspace_id + '.json', metadata)
            self.inherit_active_release(path, metadata)
            return {'workspace_id': workspace_id, 'base_commit': base,
                    'inherited_patch_sha': metadata.get('parent_patch_sha', '')}

    def inherit_active_release(self, path, meta):
        """Carry the verified release into a cumulative, fully displayed diff.

        Never overwrite user edits, mutate source HEAD, or silently accept drift.
        An existing workspace may acquire a non-conflicting parent on continuation;
        switching between two already-patched parents requires a new workspace.
        Caller holds the runner lock.
        """
        active = self.load('active.json')
        if not active:
            return
        try:
            current = self.ops.current()
        except RuntimeError:
            # Offline preparation is allowed; deploy still independently requires
            # a running, provenance-checked and healthy previous release.
            current = None
        if current and current['image'] != active['image']:
            fail('Running image changed outside the deployment journal', 409)
        if active['base_commit'] != meta['base_commit']:
            self.check_integrated_base(active, meta['base_commit'])
            return
        if meta.get('parent_patch_sha') == active['patch_sha']:
            return
        if meta.get('parent_patch_sha'):
            fail('Workspace parent release changed; prepare a new fix', 409)
        patch = active.get('patch', '')
        if not patch or hashlib.sha256(patch.encode()).hexdigest() != active['patch_sha']:
            fail('Active release patch cannot be verified', 409)
        # Validate retained work BEFORE touching it. git apply is all-or-nothing
        # without --reject; --check detects overlaps without losing any edits.
        self.diff(path, meta)
        with tempfile.NamedTemporaryFile(mode='w', dir=self.state) as target:
            target.write(patch)
            target.flush()
            check = self.command(['git', '-C', str(path), 'apply', '--check', target.name])
            if check['returncode']:
                fail('Retained edits conflict with active release; prepare a new fix', 409)
            self.git(path, 'apply', target.name)
        meta.update(parent_patch_sha=active['patch_sha'], base_image=active['image'], tests=None)
        self.save(meta['workspace_id'] + '.json', meta)

    def workspace_meta(self, workspace_id):
        if not isinstance(workspace_id, str) or not re.fullmatch(r'job-[1-9][0-9]{0,17}', workspace_id):
            fail('Invalid workspace id')
        meta = self.load(workspace_id + '.json')
        path = self.root / workspace_id
        if not meta or not path.is_dir() or path.is_symlink() or path.resolve().parent != self.root:
            fail('Unknown workspace', 404)
        head = self.git(path, 'rev-parse', '--verify', 'HEAD').strip()
        if head != meta['base_commit']:
            fail('Workspace HEAD changed', 409)
        return path, meta

    def diff(self, path, meta):
        # Include untracked editable files without committing their contents.
        untracked = self.git(path, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')
        for name in filter(None, untracked):
            safe_path(path, name, java=True)
            self.git(path, 'add', '-N', '--', name)
        names = list(filter(None, self.git(path, 'diff', '--name-only', '-z', meta['base_commit'], '--').split('\0')))
        for name in names:
            safe_path(path, name, java=True)
            lower = name.lower()
            if any(x in lower for x in ('/migration/', '/migrations/', '/db/changelog/')):
                fail('Migration changes are forbidden', 409)
            # Renames, symlinks, executable mode changes and binary diffs are not fixes.
            mode = self.git(path, 'ls-files', '--stage', '--', name)
            if mode and not mode.startswith('100644 '):
                fail('Only regular non-executable source files are allowed', 409)
            candidate = safe_path(path, name, java=True)
            if candidate.exists() and (candidate.stat().st_size > FILE_LIMIT or
                                       candidate.stat().st_mode & 0o111 or b'\0' in candidate.read_bytes()):
                fail('Source changes must be bounded text', 409)
        patch = self.git(path, 'diff', '--no-ext-diff', '--no-textconv', '--binary', '--no-renames',
                         meta['base_commit'], '--', limit=PATCH_LIMIT)
        return {'patch': patch, 'patch_sha': hashlib.sha256(patch.encode()).hexdigest(), 'changed_files': names}

    @contextlib.contextmanager
    def snapshot(self, path):
        """Copy only tracked/non-secret regular files; never mount a host repo."""
        with tempfile.TemporaryDirectory(prefix='snapshot-', dir=self.state) as directory:
            target = Path(directory)
            for name in self.source_files(path):
                if name not in {'pom.xml', 'Dockerfile', '.dockerignore'} and not name.startswith('src/'):
                    continue  # No Compose, docs, scripts or local deployment configuration in a Java sandbox.
                source = safe_path(path, name)
                if not source.exists():
                    continue
                if not source.is_file() or source.stat().st_size > 20 * 1024 * 1024:
                    fail('Unsupported build context file', 409)
                if _private_content(source.read_bytes()):
                    continue
                if _embedded_credential(source.read_bytes()):
                    fail('Tracked credential must be moved to environment configuration before sandbox execution', 409)
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                os.chmod(destination, source.stat().st_mode & 0o777)
            yield target

    def run_tests(self, path, meta, force=False):
        before = self.diff(path, meta)
        cached = meta.get('tests') or {}
        if (not force and cached.get('passed') is True and cached.get('patch_sha') == before['patch_sha']
                and cached.get('base_commit') == meta['base_commit']):
            return {**cached, 'cached': True}
        # Legacy Spring context tests need a separate DB. Always run the local
        # controller/security suite plus EVERY test file changed by this patch.
        selected = {'DoctorDemoControllerTest'}
        for filename in before['changed_files']:
            if filename.startswith('src/test/java/') and filename.endswith('.java'):
                name = Path(filename).stem
                if not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*', name):
                    fail('Invalid Java test class name')
                selected.add(name)
        with self.snapshot(path) as snapshot:
            name = 'doctor-test-' + meta['workspace_id'] + '-' + uuid.uuid4().hex[:12]
            argv = ['docker', 'run', '--rm', '--name', name, '--network', 'bridge',
                    '--memory', '1g', '--cpus', '1', '--pids-limit', '256', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--read-only',
                    '--user', f'{os.getuid()}:{os.getgid()}', '--env', 'MAVEN_CONFIG=/tmp/m2',
                    '--env', 'MAVEN_OPTS=-Djansi.mode=strip -Djansi.passthrough=true',
                    '--tmpfs', '/tmp:rw,nosuid,size=768m',
                    '--mount', f'type=bind,src={snapshot},dst=/workspace', '--workdir', '/workspace',
                    'maven:3.9-eclipse-temurin-17', 'mvn', '-B', '-ntp', '-Dmaven.repo.local=/tmp/m2/repository',
                    '-Dtest=' + ','.join(sorted(selected)), 'test']
            try:
                result = self.command(argv, timeout=300)
            finally:
                # Killing the docker client does not stop its container.
                self.command(['docker', 'rm', '-f', name], timeout=20)
            failures = self.test_failures(snapshot)
        after = self.diff(path, meta)
        tests = {'passed': result['returncode'] == 0 and not result.get('timed_out') and before == after,
                 'patch_sha': before['patch_sha'], 'base_commit': meta['base_commit'],
                   'output': redact(bounded(result['output'])), 'timed_out': result.get('timed_out', False)}
        tests['failures'] = failures
        tests['selected_tests'] = sorted(selected)
        tests['java_version'] = 17
        meta['tests'] = tests
        self.save(meta['workspace_id'] + '.json', meta)
        return tests

    @staticmethod
    def test_failures(snapshot):
        """Capture JUnit causes before deleting the sandbox. Console head/tail
        alone can lose the actual cause among expected test stack traces.
        Report files are untrusted, bounded, never followed through symlinks.
        """
        reports = snapshot / 'target' / 'surefire-reports'
        if reports.is_symlink() or (snapshot / 'target').is_symlink():
            return []
        failures, size = [], 0
        for report in sorted(reports.glob('TEST-*.xml'))[:40]:
            if report.is_symlink() or not report.is_file() or report.stat().st_size > FILE_LIMIT:
                continue
            try:
                raw = report.read_bytes()
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                    continue
                root = ET.fromstring(raw)
            except (ET.ParseError, OSError):
                continue
            for case in root.iter('testcase'):
                for failure in [*case.findall('failure'), *case.findall('error')]:
                    detail = failure.text or ''
                    causes = [line.strip()[:800] for line in detail.splitlines() if 'Caused by:' in line][-3:]
                    item = redact_data({'test': case.get('classname', '') + '.' + case.get('name', ''),
                                        'type': failure.get('type', ''), 'message': failure.get('message', '')[:1400],
                                        'causes': causes})
                    size += len(json.dumps(item))
                    if len(failures) >= 8 or size > 8000:
                        return failures
                    failures.append(item)
        return failures

    def tool(self, data):
        with self.locked():
            path, meta = self.workspace_meta(data.get('workspace_id'))
            action = data.get('action')
            if action == 'file_list':
                return self.list_files(path, data)
            if action == 'file_search':
                files = [name for name in self.source_files(path) if name.startswith('src/') or name == 'pom.xml']
                return self.search(path, files, data)
            if action == 'file_read':
                return self.read_file(path, data)
            if action in {'file_create', 'file_apply_patch'}:
                target = safe_path(path, data.get('path', ''), java=True)
                if action == 'file_create':
                    if target.exists():
                        fail('File already exists', 409)
                    content = data.get('content')
                else:
                    if not target.is_file() or target.stat().st_size > FILE_LIMIT:
                        fail('File missing or too large', 404)
                    content = target.read_text()
                    replacements = data.get('replacements')
                    if not isinstance(replacements, list) or not 1 <= len(replacements) <= 20:
                        fail('Provide 1–20 exact old/new replacements')
                    for replacement in replacements:
                        if not isinstance(replacement, dict):
                            fail('Each replacement must contain old/new text')
                        old, new = replacement.get('old'), replacement.get('new')
                        if not isinstance(old, str) or not old or not isinstance(new, str) or content.count(old) != 1:
                            fail('Each old text must match exactly once', 409)
                        content = content.replace(old, new, 1)
                if not isinstance(content, str) or len(content.encode()) > FILE_LIMIT or '\0' in content:
                    fail('Invalid Java file content')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                os.chmod(target, 0o644)
                if action == 'file_create':
                    self.git(path, 'add', '-N', '--', data['path'])
                meta['tests'] = None
                self.save(meta['workspace_id'] + '.json', meta)
                return {'path': data['path'], 'updated': True}
            if action == 'git_diff':
                return self.diff(path, meta)
            if action == 'git_status':
                return {'output': self.git(path, 'status', '--short', '--untracked-files=all')}
            if action == 'run_allowed_tests':
                return self.run_tests(path, meta, force=data.get('force') is True)
            fail('Tool is not allowlisted')

    def recover_pending_deployment(self):
        """Operator/next-confirmed-deploy recovery, deliberately not an HTTP tool.

        Caller holds locked(). Production uses real compose + health verification;
        tests may inject deployment_ops. No automatic startup app restart.
        """
        journal = self.load('deployment.json')
        if not journal or journal['status'] in {'SUCCEEDED', 'ROLLED_BACK', 'ABORTED'}:
            return journal
        if journal['status'] in {'PREPARED', 'BUILDING', 'BUILT'}:
            journal['status'] = 'ABORTED'
            self.save('deployment.json', journal)
            return journal
        return self.rollback(journal)

    def rollback(self, journal):
        journal['status'] = 'ROLLING_BACK'
        self.save('deployment.json', journal)
        try:
            self.ops.activate(journal['previous_image'])
            if self.ops.current()['image'] != journal['previous_image']:
                raise RuntimeError('Rollback did not restore the previous image')
            # The old version may still exhibit the incident; check availability only.
            verification = self.ops.verify({}, journal['started_at'], rollback=True)
            if not verification.get('ok'):
                raise RuntimeError('Previous image health verification failed')
            journal.update(status='ROLLED_BACK', rollback=verification)
            self.save('active.json', journal.get('previous_active'))
        except Exception as exc:
            journal.update(status='ROLLBACK_FAILED', rollback_error=redact(bounded(exc)))
        self.save('deployment.json', journal)
        return journal

    def check_integrated_base(self, active, base):
        if not active:
            return
        if active['base_commit'] == base:
            fail('This base has already been deployed; integrate the patch into source HEAD first', 409)
        if not active.get('patch'):
            fail('Previous patch integration cannot be verified', 409)
        ancestry = self.command(['git', '-C', str(self.source), 'merge-base', '--is-ancestor', active['base_commit'], base])
        if ancestry['returncode']:
            fail('Source HEAD does not descend from the deployed base', 409)
        # Check only: never modify the integration worktree on the user's behalf.
        with tempfile.NamedTemporaryFile(mode='w', dir=self.state) as patch:
            patch.write(active['patch'])
            patch.flush()
            integrated = self.command(['git', '-C', str(self.source), 'apply', '--reverse', '--check', patch.name])
        if integrated['returncode']:
            fail('Source HEAD does not contain the previously deployed patch', 409)

    def deploy(self, data):
        if data.get('confirmed') is not True:
            fail('Explicit confirmed=true is required', 403)
        if not isinstance(data.get('patch_sha'), str) or not re.fullmatch('[a-f0-9]{64}', data['patch_sha']):
            fail('Exact patch SHA-256 is required')
        with self.locked():
            path, meta = self.workspace_meta(data.get('workspace_id'))
            if self.head() != meta['base_commit']:
                fail('Stale workspace base commit', 409)
            if self.git(self.source, 'status', '--porcelain', '--untracked-files=all').strip():
                fail('Integration source repository is dirty', 409)
            diff = self.diff(path, meta)
            if not diff['patch'] or diff['patch_sha'] != data['patch_sha']:
                fail('Patch is empty or approval hash no longer matches', 409)
            tests = meta.get('tests') or {}
            if tests.get('passed') is not True or tests.get('patch_sha') != diff['patch_sha'] or tests.get('base_commit') != meta['base_commit']:
                fail('Exact approved patch has not passed trusted runner tests', 409)
            pending = self.recover_pending_deployment()
            if pending and pending['status'] == 'ROLLBACK_FAILED':
                fail('Previous deployment recovery failed; manual intervention required', 409)
            active = self.load('active.json')
            if active and active['base_commit'] == meta['base_commit'] and meta.get('parent_patch_sha') == active['patch_sha']:
                if diff['patch_sha'] == active['patch_sha']:
                    fail('Patch contains no changes beyond the active release', 409)
            else:
                self.check_integrated_base(active, meta['base_commit'])
            previous = self.ops.current()
            if active and previous['image'] != active['image']:
                fail('Running image changed outside the deployment journal', 409)
            if not active and previous.get('patch_sha'):
                fail('Patched image has no trusted active journal; restore its journal or use a clean provenance-labelled baseline', 409)
            if not active and previous.get('source_revision') != meta['base_commit']:
                fail('Running image source revision does not match the approved base; rebuild a provenance-labelled baseline first', 409)
            if meta.get('base_image') and previous['image'] != meta['base_image']:
                fail('Running image changed since fix preparation', 409)
            versions = {previous['image'], previous.get('source_revision', '')}
            if previous.get('patch_sha'):
                versions.add(previous.get('source_revision', '') + ':' + previous['patch_sha'])
            if meta.get('incident_version') and meta['incident_version'] not in versions:
                fail('Incident belongs to a different running release; select a current incident', 409)
            baseline = self.ops.preflight() if hasattr(self.ops, 'preflight') else {}
            if hasattr(self.ops, 'preserve'):
                self.ops.preserve(previous['image'])
            journal = {'status': 'PREPARED', 'workspace_id': meta['workspace_id'],
                       'base_commit': meta['base_commit'], 'patch_sha': diff['patch_sha'],
                       'previous_image': previous['image'], 'previous_active': active, 'baseline': baseline,
                       'started_at': datetime.now(timezone.utc).isoformat()}
            self.save('deployment.json', journal)
            try:
                journal['status'] = 'BUILDING'
                self.save('deployment.json', journal)
                with self.snapshot(path) as context:
                    # Always restore the original Dockerfile from immutable HEAD.
                    dockerfile = self.git(path, 'show', meta['base_commit'] + ':Dockerfile')
                    (context / 'Dockerfile').write_text(dockerfile)
                    image = self.ops.build(context, diff['patch_sha'])
                if (self.diff(path, meta) != diff or self.head() != meta['base_commit'] or
                        self.git(self.source, 'status', '--porcelain', '--untracked-files=all').strip()):
                    raise RuntimeError('Workspace or integration source changed during build')
                journal.update(status='SWITCHING', image=image)
                self.save('deployment.json', journal)
                self.ops.activate(image)
                if self.ops.current()['image'] != image:
                    raise RuntimeError('Compose did not activate the approved image')
                journal['status'] = 'VERIFYING'
                self.save('deployment.json', journal)
                verification = self.ops.verify(meta, journal['started_at'], rollback=False)
                if not verification.get('ok'):
                    raise RuntimeError('Deployment verification failed: ' + bounded(verification))
                journal.update(status='SUCCEEDED', verification=verification)
                # Journal is authoritative; save active before the terminal state.
                self.save('active.json', {'base_commit': meta['base_commit'], 'patch_sha': diff['patch_sha'],
                                          'patch': diff['patch'], 'image': image, 'workspace_id': meta['workspace_id']})
                self.save('deployment.json', journal)
                return journal

            except Exception as exc:
                journal['error'] = redact(bounded(exc))
                if journal['status'] in {'SWITCHING', 'VERIFYING'}:
                    return self.rollback(journal)
                journal['status'] = 'ABORTED'
                self.save('deployment.json', journal)
                return journal

    def status(self):
        status = self.ops.status()
        journal = self.load('deployment.json')
        if journal:
            status['deployment'] = {key: journal[key] for key in (
                'status', 'workspace_id', 'patch_sha', 'previous_image', 'image', 'started_at') if key in journal}
        return status

    def manual_git(self, data):
        """Human-only operation, deliberately absent from tool()/LLM allowlists.

        Source checkout/HEAD stay untouched. Branch starts at CURRENT source HEAD;
        a temporary index commits only the exact tested patch without hooks.
        """
        if data.get('confirmed') is not True:
            fail('Требуется явное подтверждение пользователя', 403)
        action, branch = data.get('action'), data.get('branch', '')
        if action not in {'branch', 'commit', 'push'}:
            fail('Неизвестное действие Git')
        if (not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_-]{0,99}', branch)
                or any(not part or part.startswith('-') for part in branch.split('/'))):
            fail('Недопустимое имя ветки')
        if not re.fullmatch(r'[a-f0-9]{64}', str(data.get('patch_sha', ''))):
            fail('Требуется точный SHA исправления')
        with self.locked():
            path, meta = self.workspace_meta(data.get('workspace_id'))
            diff = self.diff(path, meta)
            tests = meta.get('tests') or {}
            if (not diff['patch'] or diff['patch_sha'] != data['patch_sha'] or tests.get('passed') is not True
                    or tests.get('patch_sha') != diff['patch_sha'] or tests.get('base_commit') != meta['base_commit']):
                fail('Подтверждённое исправление изменилось или не прошло тесты', 409)
            if self.head() != meta['base_commit'] or self.git(self.source, 'status', '--porcelain').strip():
                fail('Текущая ветка изменилась или содержит локальные изменения', 409)
            for name in diff['changed_files']:
                candidate = safe_path(path, name, java=True)
                if candidate.exists():
                    content = candidate.read_bytes()
                    if _private_content(content) or _embedded_credential(content) or redact(content.decode()) != content.decode():
                        fail('В изменениях обнаружены секреты или персональные данные', 409)
            ref = 'refs/heads/' + branch
            def git(*args, env=None):
                result = self.command(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                                       '-C', str(path), *args], timeout=60, env=env)
                if result['returncode'] or result.get('timed_out') or result.get('truncated'):
                    fail('Действие Git не выполнено. Проверьте локальную identity, доступ и состояние ветки на хосте.', 409)
                return result['output'].strip()
            journal = meta.get('manual_git') or {}
            if action == 'branch':
                if journal:
                    fail('Для исправления уже создана ветка', 409)
                git('branch', '--', branch, meta['base_commit'])
                journal = {'branch': branch, 'base_commit': meta['base_commit'], 'patch_sha': diff['patch_sha']}
            else:
                if journal.get('branch') != branch or journal.get('patch_sha') != diff['patch_sha']:
                    fail('Сначала явно создайте ветку для этого исправления', 409)
                if action == 'commit':
                    if journal.get('commit'):
                        fail('Исправление уже закоммичено', 409)
                    if git('rev-parse', '--verify', ref) != meta['base_commit']:
                        fail('Ветка была изменена извне', 409)
                    message = data.get('message', '')
                    if (not isinstance(message, str) or not 1 <= len(message.strip()) <= 200
                            or any(ord(c) < 32 for c in message) or redact(message) != message):
                        fail('Недопустимое сообщение коммита или обнаружен секрет')
                    with tempfile.TemporaryDirectory(dir=self.state) as directory:
                        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(directory) / 'index'), 'GIT_TERMINAL_PROMPT': '0'}
                        git('read-tree', meta['base_commit'], env=env)
                        git('add', '--', *diff['changed_files'], env=env)
                        tree = git('write-tree', env=env)
                        commit = git('commit-tree', tree, '-p', meta['base_commit'], '-m', message, env=env)
                    if self.diff(path, meta) != diff:
                        fail('Исправление изменилось во время коммита', 409)
                    git('update-ref', ref, commit, meta['base_commit'])
                    journal['commit'] = commit
                else:
                    commit = journal.get('commit')
                    if not commit or git('rev-parse', '--verify', ref) != commit:
                        fail('Сначала создайте коммит; ветка не должна меняться извне', 409)
                    urls = git('remote', 'get-url', '--push', '--all', 'origin').splitlines()
                    if len(urls) != 1 or not re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?', urls[0]):
                        fail('origin должен указывать на GitHub без встроенных credentials', 409)
                    git('push', '--no-verify', '--porcelain', 'origin', commit + ':' + ref,
                        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
                    journal['pushed'] = True
            meta['manual_git'] = journal
            self.save(meta['workspace_id'] + '.json', meta)
            return {'action': action, **journal}


class DockerDeployment:
    """Fixed host operations, injectable as a group for rollback/crash tests."""
    def __init__(self, runner):
        self.runner = runner
        self.settings = runner.settings
        self.project = self.settings.compose_project
        self.service = getattr(self.settings, 'target_service', '') or self.settings.allowed_services.split(',')[0].strip()
        if not all(re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}', x) for x in (self.project, self.service)):
            fail('Invalid configured Compose project/service')

    def command(self, argv, timeout=60):
        result = self.runner.command(argv, timeout=timeout)
        if result['returncode'] or result.get('timed_out'):
            raise RuntimeError('Docker operation failed: ' + bounded(result['output']))
        return result['output']

    def current(self):
        ids = self.command(['docker', 'ps', '--filter', f'label=com.docker.compose.project={self.project}',
                            '--filter', f'label=com.docker.compose.service={self.service}', '--format', '{{.ID}}']).split()
        if len(ids) != 1 or not re.fullmatch('[a-f0-9]{12,64}', ids[0]):
            raise RuntimeError('Expected exactly one running configured Compose service')
        inspected = json.loads(self.command(['docker', 'inspect', ids[0]]))[0]
        labels = inspected.get('Config', {}).get('Labels', {})
        if labels.get('com.docker.compose.project') != self.project or labels.get('com.docker.compose.service') != self.service:
            raise RuntimeError('Container label mismatch')
        image = inspected['Image']
        if not re.fullmatch('sha256:[a-f0-9]{64}', image):
            raise RuntimeError('Expected an immutable Docker image id')
        image_labels = json.loads(self.command(['docker', 'image', 'inspect', '--format', '{{json .Config.Labels}}', image])) or {}
        return {'container_id': ids[0], 'image': image, 'running': inspected.get('State', {}).get('Running', False),
                'docker_health': inspected.get('State', {}).get('Health', {}).get('Status', 'not_configured'),
                'source_revision': image_labels.get('org.opencontainers.image.revision', ''),
                'patch_sha': image_labels.get('doctor.patch', '')}

    def build(self, context, patch_sha):
        tag = 'doctor-fix:' + patch_sha
        self.command(['docker', 'build', '--network', 'default', '--memory', '2g',
                      '--build-arg', 'SOURCE_REVISION=' + self.runner.head(),
                      '--label', 'doctor.patch=' + patch_sha, '--tag', tag,
                      '--file', str(context / 'Dockerfile'), str(context)], timeout=600)
        image = self.command(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag]).strip()
        if not re.fullmatch('sha256:[a-f0-9]{64}', image):
            raise RuntimeError('Invalid built image id')
        return image

    def preserve(self, image):
        self.command(['docker', 'image', 'tag', image, f'doctor-previous:{self.project}-{self.service}'])

    def preflight(self):
        health = self.probe(self.settings.target_health_url)
        if not health['ok']:
            raise RuntimeError('Cannot preserve a known-working image: current service is unhealthy')
        return {'health': health, 'demo': self.probe(self.settings.target_demo_url, demo=True)}

    def activate(self, image):
        if not re.fullmatch('sha256:[a-f0-9]{64}', image):
            raise RuntimeError('Only immutable image ids may be deployed')
        # Use the same infrastructure overrides as startup (e.g. local SMTP),
        # while still replacing only the allowlisted application service.
        compose_file = getattr(self.settings, 'deployment_compose_file', '') or self.settings.target_compose_file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=self.runner.state) as override:
            json.dump({'services': {self.service: {'image': image}}}, override)
            override.flush()
            self.command(['docker', 'compose', '--project-name', self.project,
                          '--file', str(Path(compose_file).resolve()),
                          '--file', override.name, 'up', '-d', '--no-deps', '--no-build',
                          '--pull', 'never', self.service], timeout=180)

    def probe(self, url, demo=False):
        if not url:
            return {'ok': True, 'skipped': True}
        if demo and not self.settings.demo_token:
            return {'ok': True, 'skipped': True, 'reason': 'Demo token not configured'}
        headers = {'X-Doctor-Demo-Token': self.settings.demo_token,
                   'X-Doctor-Demo-Trigger': 'missing-name'} if demo else {}
        try:
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                response = client.get(url, headers=headers)
                ok = 200 <= response.status_code < 300
                if demo and ok:
                    try:
                        ok = response.json().get('name') == ''
                    except (ValueError, AttributeError):
                        ok = False
                if url == self.settings.target_health_url:
                    try:
                        body = response.json()
                        if isinstance(body, dict) and str(body.get('status', '')).upper() in {'DOWN', 'OUT_OF_SERVICE', 'FAIL', 'FAILED'}:
                            ok = False
                    except ValueError:
                        pass
                return {'ok': ok, 'status': response.status_code}
        except httpx.HTTPError as exc:
            return {'ok': False, 'error': type(exc).__name__}

    def verify(self, metadata, since, rollback=False):
        health = {}
        for _ in range(max(1, getattr(self.settings, 'deployment_health_timeout_seconds', 120) // 2)):
            health = self.probe(self.settings.target_health_url)
            try:
                container = self.current()
                docker_ok = container.get('running') and container.get('docker_health', 'healthy') in {'healthy', 'not_configured'}
            except (RuntimeError, KeyError, ValueError):
                docker_ok = False
            if health['ok'] and docker_ok:
                break
            time.sleep(2)
        smoke = self.probe(self.settings.target_smoke_url)
        result = {'ok': health['ok'] and smoke['ok'] and docker_ok, 'health': health, 'smoke': smoke, 'container_healthy': bool(docker_ok)}
        if not rollback:
            demo = self.probe(self.settings.target_demo_url, demo=True)
            result.update(demo=demo, ok=result['ok'] and demo['ok'])
            container = self.current()
            log_result = self.runner.command(['docker', 'logs', '--since', since, '--tail', '200', container['container_id']], timeout=15)
            if log_result['returncode'] or log_result.get('timed_out') or log_result.get('truncated'):
                raise RuntimeError('Cannot verify bounded post-deployment logs')
            logs = log_result['output']
            exception = metadata.get('exception_type')
            result['original_exception_seen'] = self.exception_seen(logs, exception)
            result['original_fingerprint_seen'] = self.fingerprint_seen(logs, metadata, since, container['container_id'])
            result['ok'] = result['ok'] and not (result['original_exception_seen'] or result['original_fingerprint_seen'])
        return result

    @staticmethod
    def exception_seen(logs, exception):
        """Match actual exception tokens, not INFO class names such as
        ExceptionTranslationFilter, ExceptionHandler or FooExceptionFactory.
        Generic detector fallback 'Exception' must not reject every Spring boot.
        """
        if not isinstance(exception, str) or not exception:
            return False
        prefix = r'(?:[\w$]+\.)*' if '.' not in exception else ''
        token = re.compile(r'(?<![\w.$])' + prefix + re.escape(exception) + r'(?=[:\s]|$)')
        for line in logs.splitlines():
            if (re.search(r'\b(?:ERROR|FATAL)\b', line) or
                    re.match(r'\s*(?:Caused by:\s*|Suppressed:\s*)?(?:[\w$]+\.)*[\w$]*(?:Exception|Error)\b', line)):
                if token.search(line):
                    return True
        return False

    def fingerprint_seen(self, logs, metadata, since, container_id):
        expected = metadata.get('fingerprint')
        if not expected:
            return False
        # Use the detector's exact normalization. Hold version constant at the
        # original incident so a new image ID cannot hide the same exception.
        from .monitoring import JavaParser, fingerprint
        parser, events = JavaParser(), []
        for index, line in enumerate(logs.splitlines()):
            events.extend(parser.feed({'id': str(index), 'timestamp': since, 'text': line},
                                      metadata.get('incident_service') or self.service, container_id,
                                      metadata.get('incident_version', '')))
        events.extend(parser.flush(force=True))
        return any(fingerprint(event) == expected for event in events)

    def status(self):
        health = self.probe(self.settings.target_health_url)
        try:
            container = self.current()
        except (RuntimeError, ValueError, KeyError, IndexError):
            container = {'running': False, 'status': 'Configured service unavailable'}
        return {**container, 'health': health, 'healthy': container['running'] and health['ok'],
                'project': self.project, 'service': self.service}


app = FastAPI(title='Doctor trusted runner', docs_url=None, redoc_url=None, openapi_url=None)
_runner = None


def get_runner():
    global _runner
    if _runner is None:
        _runner = Runner()
    return _runner


def authenticate(x_runner_token: str = Header(default='')):
    expected = get_settings().runner_token
    if not expected or not hmac.compare_digest(x_runner_token.encode(), expected.encode()):
        fail('Runner authentication required', 401)


@app.post('/source', dependencies=[Depends(authenticate)])
def source_route(data: dict):
    return redact_data(get_runner().source_action(data))


@app.post('/workspace', dependencies=[Depends(authenticate)])
def workspace_route(data: dict):
    return get_runner().workspace(data)


@app.post('/tool', dependencies=[Depends(authenticate)])
def tool_route(data: dict):
    return get_runner().tool(data)


@app.post('/status', dependencies=[Depends(authenticate)])
def status_route():
    return get_runner().status()


@app.post('/deploy', dependencies=[Depends(authenticate)])
def deploy_route(data: dict):
    return get_runner().deploy(data)


@app.post('/manual-git', dependencies=[Depends(authenticate)])
def manual_git_route(data: dict):
    return get_runner().manual_git(data)


def recover_deployment(*, confirmed=False, runner=None):
    """Explicit operator-only helper; no network route and no startup side effect."""
    if confirmed is not True:
        fail('Explicit operator confirmation is required', 403)
    runner = runner or get_runner()
    with runner.locked():
        return runner.recover_pending_deployment()
