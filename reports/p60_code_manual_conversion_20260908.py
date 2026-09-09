"""Manually authorized P60 CodeForge conversion, using frozen existing gates."""
from pathlib import Path
import json
import argparse
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
JOBS = {
    'pulumi_patch_files_reading_v2_64k': {
        'profile': 'p60-code-pulumi-patch-files-reading-v2-64k-probe-1-v1',
        'bundle': 'configs/p16_codeforge_pulumi_failure_recovery_v1_bundle.json',
    },
    'pulumi_patch_review_64k': {
        'profile': 'p60-code-pulumi-patch-review-64k-probe-1-v1',
        'bundle': 'configs/p16_codeforge_pulumi_failure_recovery_v1_bundle.json',
    },
    'duckdb_four_tag_recovery_64k': {
        'profile': 'p60-code-duckdb-recovery-64k-probe-1-v1',
        'bundle': 'configs/p60_code_duckdb_four_tag_v1_bundle.json',
    },
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', choices=JOBS, required=True)
    options = parser.parse_args()
    job = JOBS[options.job_id]
    PROFILE = job['profile']
    BASE = ROOT / f'reports/p60_code_{options.job_id}_v1'
    OUT = ROOT / 'data/releases' / (PROFILE + '-promoted-v1')
    LOG = ROOT / f'reports/p60_code_conversion_steps_{options.job_id}_20260908.json'
    BUNDLE = ROOT / job['bundle']
    OUT.mkdir(parents=True, exist_ok=False)
    steps = []
    def run(name, script, *args):
        argv = [sys.executable, str(ROOT / 'scripts' / script), *map(str,args)]
        log_path = ROOT / f'reports/p60_code_conversion_{options.job_id}_{name}_20260908.log'
        with log_path.open('w') as handle:
            result = subprocess.run(argv, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
        steps.append({'step':name,'argv':argv,'exit_code':result.returncode,'log':str(log_path)})
        LOG.write_text(json.dumps(steps,indent=2)+'\n')
        print(json.dumps(steps[-1]),flush=True)
        if result.returncode:
            print(log_path.read_text()[-5000:],flush=True)
            raise SystemExit(result.returncode)
    run('select','promote_candidates.py','select',
        '--candidates',BASE/'audit/accepted.jsonl','--audits',BASE/'audit/audits.jsonl',
        '--release-profile',PROFILE,'--train-candidates',OUT/'train_candidates.jsonl',
        '--eval-candidates',OUT/'eval_candidates.jsonl','--train-audits',OUT/'train_audits.jsonl',
        '--eval-audits',OUT/'eval_audits.jsonl','--receipt',OUT/'release_selection.json')
    run('candidate_union','promote_candidates.py','candidate-union',
        '--candidates',OUT/'train_candidates.jsonl',OUT/'eval_candidates.jsonl',
        '--release-selection',OUT/'release_selection.json','--output',OUT/'candidate_report.json')
    for split in ['train','eval']:
        run('promote_'+split,'promote_candidates.py','promote',
            '--candidates',OUT/f'{split}_candidates.jsonl','--audits',OUT/f'{split}_audits.jsonl',
            '--output',OUT/f'{split}.jsonl','--episode-bundle',BUNDLE,
            '--expected-split',split,'--release-selection',OUT/'release_selection.json','--workers','2')
    run('report','promote_candidates.py','report','--candidate-report',OUT/'candidate_report.json',
        '--candidates',OUT/'train_candidates.jsonl',OUT/'eval_candidates.jsonl',
        '--rows',OUT/'train.jsonl',OUT/'eval.jsonl','--output',OUT/'quality_report.json',
        '--release-selection',OUT/'release_selection.json')
    run('gate','quality_gate.py','--data',OUT,'--release-profile',PROFILE,
        '--gate-receipt',OUT/'release_gate_receipt.json')
    run('b5','export_llamafactory.py','--data',OUT,'--out-dir',OUT/'llamafactory',
        '--release-root',OUT,'--train-buckets','64k','--conditions','B5',
        '--seed','0','--release-profile',PROFILE)
    run('validate_b5','validate_training_export.py','--manifest',OUT/'llamafactory/training_export_manifest.json',
        '--release-profile',PROFILE,'--expected-transform-revision','longworld-llamafactory-sharegpt-v4',
        '--required-output','B5.json','--required-output','B5.meta.json','--required-output','dataset_info.json',
        '--required-output','export_summary.json','--dataset-info-key','causaltwin_b5','--dataset-file','B5.json')

if __name__ == '__main__':
    main()
