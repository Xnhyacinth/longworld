"""Bounded process orchestration for the explicit P64 CodeForge catalog."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--exclude', default='')
    args = parser.parse_args()
    catalog = json.loads((ROOT / 'configs/p64_codeforge_taskbank_catalog_v1.json').read_text())
    env = dict(os.environ)
    env.update(LONGWORLD_PUBLIC_POLICY_SHA256=','.join(catalog['approved_policy_sha256']), LONGWORLD_GH_BINARY_SHA256=catalog['source_client_sha256'], HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false', CUDA_VISIBLE_DEVICES='')
    logs = ROOT / 'reports/p64_codeforge_taskbank_logs'
    logs.mkdir(exist_ok=True)
    def run(job):
        mode = 'validate' if args.validate else 'build'
        argv = [str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/run_with_local_probe_trust.py'), '--trust-file', catalog['trust_file'], '--role', 'source']
        for key in ('LONGWORLD_PUBLIC_POLICY_SHA256', 'LONGWORLD_GH_BINARY_SHA256', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'TOKENIZERS_PARALLELISM', 'CUDA_VISIBLE_DEVICES'):
            argv += ['--pass-env', key]
        argv += ['--', str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/materialize_codeforge_taskbank.py'), '--config', str(ROOT / job['config']), '--output', str(ROOT / job['output'])]
        if args.validate:
            argv.append('--validate')
        result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        (logs / f"{job['repository']}.{mode}.stdout.txt").write_text(result.stdout)
        (logs / f"{job['repository']}.{mode}.stderr.txt").write_text(result.stderr)
        row = {'repository': job['repository'], 'mode': mode, 'exit_code': result.returncode}
        if result.returncode == 0:
            row['result'] = json.loads(result.stdout)
        print(json.dumps(row), flush=True)
        return row
    jobs = [j for j in catalog['jobs'] if j['repository'] not in args.exclude.split(',')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, jobs))
    suffix = 'validate' if args.validate else 'build'
    (logs / f'{suffix}_summary.json').write_text(json.dumps(results, indent=2) + '\n')
    if any(r['exit_code'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
