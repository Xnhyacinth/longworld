"""Bounded source fetch using the existing pinned GitHub exporter."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.attestation import attach_attestation, attestation_key_from_env

PULLS = (34817, 34692, 32587, 34833, 34861)


def main():
    out = ROOT / 'data/source_inventory/p64_codeforge_scikit_workflows_v1'
    out.mkdir(parents=True, exist_ok=True)
    logs = ROOT / 'reports/p64_codeforge_scikit_fetch_logs'
    logs.mkdir(exist_ok=True)
    def fetch(number):
        path = out / f'scikit_learn_pr{number}.json'
        if path.exists():
            raise FileExistsError(path)
        argv = [sys.executable, str(ROOT / 'scripts/export_github_workflow.py'), '--allowlist', str(ROOT / 'configs/public_repo_allowlist.yaml'), '--repo', 'scikit-learn/scikit-learn', '--pull', str(number), '--out', str(path)]
        result = subprocess.run(argv, cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, check=False)
        (logs / f'{number}.stdout.txt').write_text(result.stdout)
        (logs / f'{number}.stderr.txt').write_text(result.stderr)
        row = {'pull': number, 'exit_code': result.returncode}
        if result.returncode == 0:
            row['path'] = str(path.relative_to(ROOT))
            row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(json.dumps(row), flush=True)
        return row
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(fetch, PULLS))
    (logs / 'FETCH_RECEIPT.json').write_text(json.dumps(results, indent=2) + '\n')
    passed = [r for r in results if r['exit_code'] == 0]
    if len(passed) < 2:
        raise ValueError('insufficient verified new-repository episodes')
    bundle = {'schema_version': 'longworld.episode-replay-bundle.v1', 'composition': 'chronological_causal_union', 'path_base': 'repository_root', 'episodes': [{'path': r['path'], 'sha256': r['sha256']} for r in passed]}
    bundle = attach_attestation(bundle, attestation_key_from_env('episode_replay_bundle'), purpose='episode_replay_bundle')
    (ROOT / 'configs/p64_codeforge_scikit_source_bundle_v1.json').write_text(json.dumps(bundle, indent=2) + '\n')


if __name__ == '__main__':
    main()
