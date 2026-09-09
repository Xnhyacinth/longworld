"""Source-bound capacity and native replay preflight for two P60 code jobs."""
from pathlib import Path
import hashlib
import re
import argparse
import json
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.realworkflow import load_episode_replay_bundle
from longworld.core.engine import answer_from_artifacts
from longworld.core.sampler import materialize
from longworld.core.views import render_cf_view
from transformers import AutoTokenizer


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--job-id',required=True)
    options=parser.parse_args()
    catalog = json.loads((ROOT/'configs/p60_code_pulumi_patch_files_reading_v2_job.json').read_text())
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
        visible_outputs=[]
        for q in selected:
            by_event={event.id:event for event in world.events}
            by_record={event.params.get('record_key'):event for event in world.events if event.type=='repo_record'}
            by_artifact_event={event_id:artifact for artifact in artifacts for event_id in artifact.reveals_events}
            cycles=[]
            for op in q.program_ops:
                tag=world.state.values.get(f"real:release:{op['release_record_key']}:tag")
                review=world.state.values.get(f"real:review:{op['review_record_key']}:decision")
                test=world.state.values.get(f"repo:{op['ci_record_key']}:test")
                result_value=world.state.values.get(f"repo:{op['ci_record_key']}:result")
                release_text=by_artifact_event[by_record[op['release_record_key']].id].text
                review_text=by_artifact_event[by_record[op['review_record_key']].id].text
                ci_text=by_artifact_event[by_record[op['ci_record_key']].id].text
                cycles.append({'tag_visible':str(tag) in release_text,'review_visible_casefold':str(review).lower() in review_text.lower(),'test_name_visible':str(test) in ci_text,'test_result_visible':result_value in ci_text or ('conclusion=success' in ci_text if result_value=='passed' else 'conclusion=failure' in ci_text)})
            cf_world,cf_artifacts=render_cf_view(world,q)
            cf_text=next(a.text for a in cf_artifacts if q.cf_event_id in a.reveals_events)
            full_text=by_artifact_event[q.cf_event_id].text
            cf_visible=full_text!=cf_text and 'conclusion=failed' in cf_text
            cf_replay=answer_from_artifacts(cf_world,q,cf_artifacts,enforce_preconditions=True)
            visible_outputs.append({'query_id':q.query_id,'cycles':cycles,'no_ancestry_target':';ancestry=' not in q.answer,'all_output_identifier_tokens_visible':all(identifier in '\n'.join(a.text for a in artifacts) for identifier in re.findall(r'\b[0-9a-f]{40,64}\b',q.answer)),'cf_event_kind':by_event[q.cf_event_id].params['record_kind'],'cf_visible_fact_changed':cf_visible,'cf_replay_matches_changed_answer':cf_replay==q.cf_answer and cf_replay!=q.answer})
        result['reading_output_contract_checks']=visible_outputs
        result['preflight_ok']=result['preflight_ok'] and all(all(all(c.values()) for c in r['cycles']) and r['no_ancestry_target'] and r['all_output_identifier_tokens_visible'] and r['cf_visible_fact_changed'] and r['cf_replay_matches_changed_answer'] for r in visible_outputs)
        result['existing_query_comparison']=comparison
        base=ROOT/f"reports/p60_code_{job['job_id']}_v1"
        base.mkdir(parents=True,exist_ok=True)
        (base/'preflight.json').write_text(json.dumps(result,indent=2)+'\n')
        results.append(result)
        print(json.dumps({k:v for k,v in result.items() if k not in {'replay','source_checks','job'}}),flush=True)
        (ROOT/f'reports/p60_code_source_preflight_{options.job_id}_20260908.json').write_text(json.dumps(results,indent=2)+'\n')

if __name__=='__main__':main()
