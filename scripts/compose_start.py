"""Start infrastructure without silently replacing an already-approved release."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from doctor.config import get_settings


def main():
    cfg = get_settings()
    source = Path(cfg.source_repo)
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    state = Path(cfg.workspace_root) / '.runner-state'
    journal = json.loads((state / 'deployment.json').read_text()) if (state / 'deployment.json').exists() else None
    if journal and journal.get('status') not in {'SUCCEEDED', 'ROLLED_BACK', 'ABORTED'}:
        raise SystemExit('Unfinished deployment journal: inspect runner status and perform explicit operator recovery first.')
    active = json.loads((state / 'active.json').read_text()) if (state / 'active.json').exists() else None
    env = {**os.environ, 'SOURCE_REVISION': revision}
    if active:
        subprocess.run(['docker', 'image', 'inspect', active['image']], stdout=subprocess.DEVNULL, check=True)
        override = Path('.runtime/current-release.json')
        override.write_text(json.dumps({'services': {cfg.allowed_services.split(',')[0]: {'image': active['image']}}}))
        subprocess.run(['docker', 'compose', '-f', 'compose.yaml', '-f', str(override), 'up', '-d', '--no-build', 'mysql', cfg.allowed_services.split(',')[0]], env=env, check=True)
        subprocess.run(['docker', 'compose', 'up', '-d', '--build', 'api', 'frontend'], env=env, check=True)
    else:
        dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True)
        if dirty:
            raise SystemExit('Integration source is dirty: commit/review it before building a provenance-labelled baseline.')
        subprocess.run(['docker', 'compose', 'up', '-d', '--build'], env=env, check=True)


if __name__ == '__main__':
    main()
