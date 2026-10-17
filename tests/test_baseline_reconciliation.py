from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
import zipfile

import pytest

from scripts import reconcile_baseline as module


IMAGE = 'sha256:' + '1' * 64
BUILT = 'sha256:' + '2' * 64
REVISION = 'a' * 40


def write_jar(path, content=b'class bytes', date=(2026, 1, 1, 0, 0, 0)):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('BOOT-INF/classes/example/Service.class', date), content)
        archive.writestr('META-INF/MANIFEST.MF', 'Main-Class: org.springframework.boot.loader.JarLauncher\n')


@pytest.fixture
def baseline(tmp_path, monkeypatch):
    current = {'image': IMAGE, 'source_revision': REVISION, 'patch_sha': '',
               'container_id': 'abcdef123456', 'running': True, 'docker_health': 'healthy'}
    state = {'active.json': {'image': 'old-approved', 'patch': 'retained patch'},
             'deployment.json': {'status': 'SUCCEEDED', 'image': 'old-approved'}}
    commands, saves = [], []
    h = SimpleNamespace(current=current, state=state, commands=commands, saves=saves,
                        dirty=False, different_jar=False, healthy=True)

    def command(argv, **kwargs):
        commands.append(argv)
        if argv[1:3] == ['image', 'inspect']:
            return BUILT
        if argv[1] == 'create':
            return 'b' * 64
        if argv[1] == 'cp':
            content = b'different class' if h.different_jar and argv[-1].endswith('built.jar') else b'class bytes'
            write_jar(argv[-1], content)
        return ''

    def save(name, data):
        saves.append(name)
        state[name] = deepcopy(data)

    runner = SimpleNamespace(source=tmp_path, state=tmp_path, head=lambda: REVISION,
                             settings=SimpleNamespace(target_demo_url='http://local/demo'),
                             git=lambda *args: 'modified' if h.dirty else '',
                             load=state.get, save=save, locked=nullcontext,
                             snapshot=lambda source: nullcontext(source))
    runner.ops = SimpleNamespace(current=lambda: deepcopy(current), command=command,
                                 verify=lambda *args, **kwargs: {'ok': h.healthy},
                                 probe=lambda *args, **kwargs: {'ok': h.healthy})
    h.runner = runner
    monkeypatch.setattr(module, 'require_api_idle', lambda runner: None)
    return h


def test_baseline_adoption_archives_journals_without_restarting_or_removing_workspaces(baseline):
    h = baseline
    original = deepcopy(h.state)
    retained = h.runner.source / 'retained.patch'
    retained.write_text('user work')
    result = module.reconcile(h.runner, IMAGE, confirmed=True)
    assert result['status'] == 'SUCCEEDED'
    audit = h.state[result['audit']]
    assert audit['previous_active'] == original['active.json']
    assert audit['previous_deployment'] == original['deployment.json']
    assert audit['jar_entries'] == 2
    assert audit['verification_image'] == BUILT and audit['confirmed'] is True
    assert h.state['active.json'] is None
    assert h.state['deployment.json']['operation'] == 'BASELINE_ADOPTION'
    assert h.saves[0] == result['audit'] and h.saves[-1] == result['audit']
    assert retained.read_text() == 'user work'
    assert not any(argv[1] in {'stop', 'restart', 'compose'} for argv in h.commands)
    assert [argv for argv in h.commands if argv[1] == 'rm'] == [['docker', 'rm', 'b' * 64]]


def test_baseline_requires_explicit_confirmation(baseline):
    with pytest.raises(RuntimeError, match='approval'):
        module.reconcile(baseline.runner, IMAGE)
    assert not baseline.commands and not baseline.saves


@pytest.mark.parametrize('change, message', [
    ({'image': BUILT}, 'explicit operator approval'),
    ({'source_revision': 'c' * 40}, 'unpatched baseline'),
    ({'patch_sha': 'd' * 64}, 'unpatched baseline'),
    ({'docker_health': 'starting'}, 'healthy'),
    ({'running': False}, 'healthy'),
])
def test_mismatched_or_unhealthy_image_cannot_be_adopted(baseline, change, message):
    baseline.current.update(change)
    with pytest.raises(RuntimeError, match=message):
        module.reconcile(baseline.runner, IMAGE, confirmed=True)
    assert not baseline.commands and not baseline.saves


def test_dirty_source_and_unsettled_deployment_are_rejected(baseline):
    baseline.dirty = True
    with pytest.raises(RuntimeError, match='clean'):
        module.reconcile(baseline.runner, IMAGE, confirmed=True)
    baseline.dirty = False
    baseline.state['deployment.json']['status'] = 'ROLLBACK_FAILED'
    with pytest.raises(RuntimeError, match='Unfinished'):
        module.reconcile(baseline.runner, IMAGE, confirmed=True)
    assert not baseline.saves


def test_different_application_artifact_never_changes_trusted_journals(baseline):
    baseline.different_jar = True
    before = deepcopy(baseline.state)
    with pytest.raises(RuntimeError, match='differs from clean source'):
        module.reconcile(baseline.runner, IMAGE, confirmed=True)
    assert baseline.state == before and not baseline.saves


def test_source_is_rechecked_after_build_before_adoption(baseline):
    def verify(*args, **kwargs):
        baseline.dirty = True
        return {'ok': True}
    baseline.runner.ops.verify = verify
    with pytest.raises(RuntimeError, match='clean'):
        module.reconcile(baseline.runner, IMAGE, confirmed=True)
    assert not baseline.saves


def test_equivalent_replacement_records_provenance_and_keeps_previous_journals(baseline):
    h = baseline
    h.current['source_revision'] = 'c' * 40
    activated = []
    def activate(image):
        activated.append(image)
        h.current.update(image=image, source_revision=REVISION)
    h.runner.ops.activate = activate
    original = deepcopy(h.state)
    result = module.reconcile(h.runner, IMAGE, confirmed=True, replace_equivalent=True)
    assert activated == [BUILT] and result['image'] == BUILT
    assert h.state['active.json'] is None
    audit = h.state[result['audit']]
    assert audit['previous_active'] == original['active.json']
    assert audit['previous_deployment'] == original['deployment.json']
    assert audit['previous_image'] == IMAGE and audit['base_commit'] == REVISION
    assert h.state['deployment.json']['status'] == 'SUCCEEDED'


def test_failed_equivalent_replacement_rolls_back_without_adopting(baseline):
    h = baseline
    original = deepcopy(h.state)
    activated = []
    def activate(image):
        activated.append(image)
        h.current['image'] = image
    def rollback(journal):
        activate(journal['previous_image'])
        h.runner.save('active.json', journal['previous_active'])
        h.runner.save('deployment.json', {**journal, 'status': 'ROLLED_BACK'})
        return h.state['deployment.json']
    h.runner.ops.activate = activate
    h.runner.ops.verify = lambda *args, **kwargs: {'ok': h.current['image'] == IMAGE}
    h.runner.rollback = rollback
    with pytest.raises(RuntimeError, match='failed verification'):
        module.reconcile(h.runner, IMAGE, confirmed=True, replace_equivalent=True)
    assert activated == [BUILT, IMAGE]
    assert h.state['active.json'] == original['active.json']
    assert h.state['deployment.json']['status'] == 'ROLLED_BACK'
    audits = [row for name, row in h.state.items() if name.startswith('baseline-adoption-')]
    assert len(audits) == 1 and audits[0]['status'] == 'ROLLED_BACK'


@pytest.mark.parametrize('reply', ['BUSY', 'unexpected output'])
def test_api_must_be_confirmed_idle(reply):
    commands = []
    def command(argv):
        commands.append(argv)
        return 'api-container' if argv[1] == 'ps' else reply
    runner = SimpleNamespace(settings=SimpleNamespace(compose_project='fixed-project'),
                             ops=SimpleNamespace(command=command))
    with pytest.raises(RuntimeError, match='Active or unsettled'):
        module.require_api_idle(runner)
    assert 'label=com.docker.compose.project=fixed-project' in commands[0]
    assert commands[1][:3] == ['docker', 'exec', 'api-container']


def test_jar_manifest_ignores_only_zip_metadata(tmp_path):
    first, second = tmp_path / 'first.jar', tmp_path / 'second.jar'
    write_jar(first)
    write_jar(second, date=(2026, 2, 2, 0, 0, 0))
    assert module.jar_manifest(first) == module.jar_manifest(second)
    write_jar(second, content=b'changed bytecode')
    assert module.jar_manifest(first) != module.jar_manifest(second)


def test_duplicate_jar_entries_are_rejected(tmp_path):
    path = tmp_path / 'duplicate.jar'
    write_jar(path)
    with zipfile.ZipFile(path, 'a') as archive:
        with pytest.warns(UserWarning, match='Duplicate'):
            archive.writestr('BOOT-INF/classes/example/Service.class', b'another class')
    with pytest.raises(RuntimeError, match='Duplicate'):
        module.jar_manifest(path)
