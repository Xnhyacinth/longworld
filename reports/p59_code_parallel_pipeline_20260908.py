"""Bounded independent CodeForge candidate pipelines; no automatic promotion."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import argparse
import subprocess
import sys
import yaml

ROOT=Path(__file__).resolve().parents[1]

def execute(job):
    job_id=job['job_id']
    base=ROOT/f'reports/p59_code_{job_id}_v1'
    base.mkdir(parents=True,exist_ok=False)
    cfg=yaml.safe_load((ROOT/job['template']).read_text())
    cfg['data_product']=f'worldlong_p59_code_{job_id}_v1'
    cfg['real_workflow_query_types']=[job['query_type']]
    cfg['real_workflow_query_length_buckets']={job['query_type']:job['bands']}
    cfg['real_workflow_length_buckets']=job['bands']
    cfg['real_workflow_bundle']=job['bundle']
    cfg['length_buckets']={key:value for key,value in cfg['length_buckets'].items() if key in job['bands']}
    cfg['release_min_evidence_tokens']={key:value for key,value in cfg['release_min_evidence_tokens'].items() if key in job['bands']}
    config=ROOT/f'configs/p59_code_{job_id}_v1.yaml'
    config.write_text(yaml.safe_dump(cfg,sort_keys=False))
    steps=[]
    def run(name,script,*args):
        argv=[sys.executable,str(ROOT/'scripts'/script),*map(str,args)]
        log=base/f'{name}.log'
        with log.open('w') as handle:code=subprocess.run(argv,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT).returncode
        steps.append({'step':name,'argv':argv,'exit_code':code,'log':str(log)})
        (base/'steps.json').write_text(json.dumps(steps,indent=2)+'\n')
        print(json.dumps({'job_id':job_id,**steps[-1]}),flush=True)
        if code:raise RuntimeError(f'{job_id}: {name} exit {code}; see {log}')
    run('generate','generate.py','--config',config,'--seed-start',job['seed'],'--out-dir',base/'candidate','--workers','2')
    candidate=base/'candidate/train.jsonl'
    if not candidate.read_text().strip():return {'job_id':job_id,'status':'no_candidates','base':str(base)}
    run('rank','rank_candidates_dense.py','--candidates',candidate,'--output',base/'audit/rankings.jsonl','--model-id','sentence-transformers/all-MiniLM-L6-v2','--revision','1110a243fdf4706b3f48f1d95db1a4f5529b4d41','--batch-size','32')
    run('audit','promote_candidates.py','audit','--candidates',candidate,'--rankings',base/'audit/rankings.jsonl','--output',base/'audit/audits.jsonl','--accepted-candidates',base/'audit/accepted.jsonl','--rejects',base/'audit/rejects.jsonl','--top-k','3','--release-profile','p7-github-source-slice-1-v1','--episode-bundle',ROOT/job['bundle'],'--workers','2')
    return {'job_id':job_id,'status':'audited','base':str(base),'candidate_rows':len(candidate.read_text().splitlines()),'accepted_rows':len((base/'audit/accepted.jsonl').read_text().splitlines()),'rejected_rows':len((base/'audit/rejects.jsonl').read_text().splitlines())}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--job-id')
    parser.add_argument('--preflight',type=Path,default=ROOT/'reports/p59_code_source_preflight_20260908.json')
    args=parser.parse_args()
    preflight=json.loads(args.preflight.read_text())
    jobs=[item['job'] for item in preflight if item['preflight_ok'] and (args.job_id is None or item['job']['job_id']==args.job_id)]
    if not jobs:raise ValueError('no preflight-passed jobs selected')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,job) for job in jobs]
        results=[]
        for future in futures:
            try:results.append(future.result())
            except Exception as exc:results.append({'status':'blocked','error':str(exc)})
    (ROOT/f'reports/p59_code_parallel_pipeline_{args.job_id or "all"}_20260908.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2),flush=True)

if __name__=='__main__':main()
