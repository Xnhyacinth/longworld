"""Source-bound capacity and native replay preflight for two P60 code jobs."""
from pathlib import Path
import hashlib
import argparse
import json
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.realworkflow import load_episode_replay_bundle
from longworld.core.engine import answer_from_artifacts
from longworld.core.sampler import materialize
from transformers import AutoTokenizer


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--job-id',required=True)
    options=parser.parse_args()
    catalog = json.loads((ROOT/'configs/p60_code_pulumi_patch_files_job_v1.json').read_text())
    catalog['jobs']=[j for j in catalog['jobs'] if j['job_id']==options.job_id]
    if not catalog['jobs']:raise ValueError('unknown job')
    tokenizer = AutoTokenizer.from_pretrained('Qwen/Qwen3.5-4B',revision='a7b0d22b993d71000cf2eadfb37222a67cee521e',local_files_only=True)
    results=[]
    for job in catalog['jobs']:
        bundle_path=ROOT/job['bundle']
        bundle=json.loads(bundle_path.read_text())
        texts={};checks=[]
        for item in bundle['episodes']:
            p=ROOT/item['path'];payload=json.loads(p.read_text());actual=hashlib.sha256(p.read_bytes()).hexdigest()
            checks.append({'path':item['path'],'sha256':actual,'matches':actual==item['sha256'],'repository':payload['repository_url']})
            for record in payload['records']:
                texts[hashlib.sha256(record['text'].encode()).hexdigest()]=record['text']
        workflows=load_episode_replay_bundle(bundle_path)
        materialized=materialize(job['seed'],n_parallel=0,n_pulses=0,n_workstreams=0,domain='codeforge',real_workflows=workflows,include_program_joins=False)
        world=materialized.worlds['focal']
        queries=materialized.queries
        selected=[q for q in queries if q.query_type==job['query_type'] and set(q.preferred_length_buckets)&set(job['bands'])]
        artifacts=materialized.artifacts['focal']
        replay=[]
        for q in selected:
            sufficient=[a for a in artifacts if set(a.reveals_events)&set(q.sufficient_event_ids)]
            factual=answer_from_artifacts(world,q,sufficient,enforce_preconditions=True)
            removals={event:answer_from_artifacts(world,q,sufficient,skip_ids={event},enforce_preconditions=True)!=factual for event in q.essential_event_ids}
            source_groups={event.params.get('workflow_id') for event in world.events if event.id in q.sufficient_event_ids}
            group_removed={str(group):answer_from_artifacts(world,q,sufficient,skip_ids={event.id for event in world.events if event.params.get('workflow_id')==group},enforce_preconditions=True)!=factual for group in source_groups}
            replay.append({'query_id':q.query_id,'bands':q.preferred_length_buckets,'full_matches':factual==q.answer,'cf_changes':q.cf_answer!=q.answer,'empty_replay_matches':answer_from_artifacts(world,q,[],enforce_preconditions=True)==q.answer,'question_contains_full_answer':q.answer in q.question,'has_single_code_per_field_marker':'Use this exact per-field output codebook' in q.question,'essential_events':len(q.essential_event_ids),'remove_essential_event_changes':removals,'remove_source_workflow_changes':group_removed})
        result={'job':job,'bundle_sha256':hashlib.sha256(bundle_path.read_bytes()).hexdigest(),'source_checks':checks,'unique_text_bodies':len(texts),'exact_unique_source_tokens':sum(len(tokenizer.encode(text,add_special_tokens=False)) for text in texts.values()),'native_query_types':dict(Counter(q.query_type for q in queries)),'selected_query_count':len(selected),'replay':replay,'materialization':'shared sampler materialize including causal closure and rendered artifact integrity', 'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'limitation':'Native no-evidence replay and exact answer-in-question checks are not a measured LLM question-only evaluation. Source capacity is not packed candidate capacity.'}
        result['preflight_ok']=bool(selected) and all(c['matches'] for c in checks) and all(r['full_matches'] and r['cf_changes'] and not r['empty_replay_matches'] and not r['question_contains_full_answer'] and not r['has_single_code_per_field_marker'] and all(r['remove_essential_event_changes'].values()) and all(r['remove_source_workflow_changes'].values()) for r in replay)
        comparison=[]
        if job.get('compare_query_type'):
            existing=[q for q in queries if q.query_type==job['compare_query_type'] and set(q.preferred_length_buckets)&set(job['bands'])]
            mapping={'patch_record_key':'repair_commit_record_key','review_record_key':'review_record_key','ci_record_key':'pass_ci_record_key','merge_record_key':'merge_record_key','release_record_key':'release_record_key'}
            for q in selected:
                for old in existing:
                    mapped_same=len(q.program_ops)==len(old.program_ops) and all(all(new_op.get(new_key)==old_op.get(old_key) for new_key,old_key in mapping.items()) for new_op,old_op in zip(q.program_ops,old.program_ops))
                    comparison.append({'new_query_id':q.query_id,'existing_query_id':old.query_id,'same_answer':q.answer==old.answer,'new_answer':q.answer,'existing_answer':old.answer,'essential_evidence_subset':set(q.essential_event_ids)<=set(old.essential_event_ids),'same_counterfactual_event':q.cf_event_id==old.cf_event_id,'same_mapped_release_local_ops':mapped_same,'different_evidence_ids':sorted(set(q.essential_event_ids)-set(old.essential_event_ids)),'semantic_projection_duplicate':mapped_same and set(q.essential_event_ids)<=set(old.essential_event_ids) and q.cf_event_id==old.cf_event_id})
            if comparison and all(c['semantic_projection_duplicate'] for c in comparison):
                result['preflight_ok']=False
                result['decision']='SKIP_SEMANTIC_PROJECTION_DUPLICATE'
        readable_paths=[]
        for q in selected:
            paths=[path for op in q.program_ops for path in world.state.values.get(f"real:patch:{op['patch_record_key']}:files", ())]
            source_text='\n'.join(a.text for a in artifacts if set(a.reveals_events)&set(q.sufficient_event_ids))
            readable_paths.append({'query_id':q.query_id,'n_output_paths':len(paths),'all_paths_visible_in_source':all(path in source_text for path in paths),'no_digest_output':';patch=' not in q.answer and 'SHA256' not in q.question,'paths':paths})
        result['readable_path_checks']=readable_paths
        result['preflight_ok']=result['preflight_ok'] and bool(readable_paths) and all(r['n_output_paths']>0 and r['all_paths_visible_in_source'] and r['no_digest_output'] for r in readable_paths)
        result['existing_query_comparison']=comparison
        base=ROOT/f"reports/p60_code_{job['job_id']}_v1"
        base.mkdir(parents=True,exist_ok=True)
        (base/'preflight.json').write_text(json.dumps(result,indent=2)+'\n')
        results.append(result)
        print(json.dumps({k:v for k,v in result.items() if k not in {'replay','source_checks','job'}}),flush=True)
        (ROOT/f'reports/p60_code_source_preflight_{options.job_id}_20260908.json').write_text(json.dumps(results,indent=2)+'\n')

if __name__=='__main__':main()
