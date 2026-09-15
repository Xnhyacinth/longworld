"""Re-audit frozen P58 hash-family representatives without changing old files."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.attestation import attestation_key_from_env
from longworld.core.promotion import candidate_sha256, create_dense_audit


def main():
    base = ROOT / 'reports/p58_code_transformers_review_ancestry_v1'
    paths = [base/'candidate/train.jsonl', base/'audit/rankings.jsonl', base/'audit/audits.jsonl']
    before = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rows = [r for r in map(json.loads,paths[0].read_text().splitlines()) if r['view']=='full']
    rankings = {r['candidate_sha256']:r for r in map(json.loads,paths[1].read_text().splitlines())}
    old_audits = {r['candidate_sha256']:r for r in map(json.loads,paths[2].read_text().splitlines())}
    results=[]
    for row in rows:
        digest=candidate_sha256(row)
        audit=create_dense_audit(row,rankings[digest],k=3,
            episode_bundle_path=ROOT/'configs/p17_codeforge_transformers_failure_recovery_v1_bundle.json',
            candidate_attestation_key=attestation_key_from_env('candidate_row'),
            ranking_attestation_key=attestation_key_from_env('dense_ranking'),
            audit_attestation_key=attestation_key_from_env('dense_retrieval_audit'),
            episode_attestation_key=attestation_key_from_env('episode_replay_bundle'))
        old=old_audits[digest]
        results.append({'query_id':row['query_id'],'length_bucket':row['length_bucket'],'audit_exactly_matches_frozen_receipt':audit==old,'verification_replay_sha256_matches':audit['verification_replay_sha256']==old['verification_replay_sha256'],'strict_answer_matches':audit['strict_replay_answer']==old['strict_replay_answer']})
    result={'rows':results,'input_sha256':before,'frozen_files_unchanged':all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in before.items())}
    (ROOT/'reports/p60_code_legacy_readback_20260908.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not result['frozen_files_unchanged'] or not all(r['audit_exactly_matches_frozen_receipt'] for r in results):raise SystemExit(1)

if __name__=='__main__':main()
