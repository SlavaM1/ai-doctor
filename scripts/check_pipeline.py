"""Bounded live monitoring smoke. Does not request fixes or approve deployments."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from doctor.config import get_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-trigger', action='store_true')
    args = parser.parse_args()
    settings = get_settings()
    api = settings.api_url
    with httpx.Client(timeout=15) as client:
        client.get(api + '/health').raise_for_status()
        for _ in range(10):
            target = client.get(api + '/status').json().get('target', {})
            if target.get('source_revision') or target.get('image'):
                break
            time.sleep(1)
        else:
            raise SystemExit('Trusted runner unavailable; start host services before testing the pipeline')
        version = target.get('source_revision') or target.get('image', '')
        if target.get('patch_sha'):
            version += ':' + target['patch_sha']
        if not args.no_trigger:
            response = client.get(settings.target_demo_url, headers={'X-Doctor-Demo-Token': settings.demo_token, 'X-Doctor-Demo-Trigger': 'missing-name'})
            if response.status_code != 500:
                raise SystemExit(f'Expected controlled pre-fix 500, got {response.status_code}; do not treat a fixed release as a failed collector.')
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            incidents = client.get(api + '/incidents').json()
            selected = next((i for i in incidents if i.get('version') == version and 'DemoNameNormalizer.java' in i.get('event', {}).get('stack_trace', '')), None)
            if selected and selected['diagnosis_status'] in {'DONE', 'FAILED'}:
                break
            time.sleep(2)
        else:
            raise SystemExit('Timed out waiting for real incident diagnosis')
        detail = client.get(api + f"/incidents/{selected['id']}").json()
        runs_before = len([r for r in detail['agent_runs'] if r['kind'] == 'diagnosis'])
        before = selected['occurrences']
        if not args.no_trigger:
            for _ in range(3):
                response = client.get(settings.target_demo_url, headers={'X-Doctor-Demo-Token': settings.demo_token, 'X-Doctor-Demo-Trigger': 'missing-name'})
                assert response.status_code == 500
            end = time.monotonic() + 20
            while time.monotonic() < end:
                detail = client.get(api + f"/incidents/{selected['id']}").json()
                if detail['incident']['occurrences'] >= before + 3:
                    break
                time.sleep(1)
            assert detail['incident']['occurrences'] >= before + 3, 'Collector did not receive repeated runtime errors'
            assert len([r for r in detail['agent_runs'] if r['kind'] == 'diagnosis']) == runs_before, 'Duplicate diagnosis calls'
        assert detail['incident']['diagnosis_status'] == 'DONE', 'Live diagnosis failed; inspect Dashboard'
        evidence = {'incident_id': selected['id'], 'exception': selected['exception_type'], 'occurrences': detail['incident']['occurrences'],
                    'diagnosis': detail['diagnoses'][0], 'runs': [r for r in detail['agent_runs'] if r['kind'] == 'diagnosis']}
        Path('.runtime').mkdir(exist_ok=True)
        Path('.runtime/pipeline-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(json.dumps({key: evidence[key] for key in ('incident_id', 'exception', 'occurrences', 'runs')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
