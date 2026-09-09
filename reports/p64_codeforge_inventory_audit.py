"""Account source groups, semantic tasks, and natural context sizes separately."""
from collections import Counter
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]


def main():
    catalog=json.loads((ROOT/'configs/p64_codeforge_taskbank_catalog_v1.json').read_text())
    prior_path=ROOT/'reports/p62_world_diversity_inventory_20260909.json'
    prior=json.loads(prior_path.read_text())
    prior_repos=set(prior['mapped_repos'])
    rows=[];all_tasks=[];all_contexts=[]
    for job in catalog['jobs']:
        base=ROOT/job['output']
        receipt=json.loads((base/'BUILD_RECEIPT.json').read_text())
        world=json.loads((base/'world.json').read_text())
        tasks=[json.loads(x) for x in (base/'tasks.jsonl').read_text().splitlines()]
        contexts=[json.loads(x) for x in (base/'contexts.jsonl').read_text().splitlines()]
        by_context={c['context_id']:c for c in contexts}
        repo=world['source_group_id']
        old=repo.removeprefix('https://') in prior_repos
        newly_fetched=job['repository']=='opensearch'
        exact=[]
        for context in contexts:
            if context['legacy_exact_bands']:
                ts=[t for t in tasks if t['context_id']==context['context_id']]
                exact.append({'context_id':context['context_id'],'episode_ids':context['episode_ids'],'exact_context_tokens':context['exact_context_tokens'],'bands':context['legacy_exact_bands'],'tasks':len(ts),'programs':sorted({t['program_id'] for t in ts})})
        rows.append({'repository':repo,'directory':str(base.relative_to(ROOT)),'split':receipt['split'],'source_collection_id':world['source_collection_id'],'world_instance_id':world['world_instance_id'],'previously_qualified_repo_in_p62':old,'newly_fetched_source_collection_in_p64':newly_fetched,'novelty_class':'newly_fetched_repository_source_collection' if newly_fetched else ('existing_qualified_repo_reused_frozen_sources' if old else 'new_to_qualified_inventory_but_reused_frozen_sources'),'source_episode_count':receipt['source_episode_count'],'semantic_task_count':len(tasks),'sample_count':len(tasks),'primary_samples_per_semantic_task':1,'variant_families':len({t['variant_family_id'] for t in tasks}),'program_counts':dict(Counter(t['program_id'] for t in tasks)),'sample_capacity_counts':dict(Counter(by_context[t['context_id']]['smallest_capacity_bin'] for t in tasks)),'context_capacity_counts':receipt['context_capacity_counts'],'exact_band_contexts':exact,'max_exact_context_tokens':receipt['max_exact_context_tokens'],'raw_contexts_over_256k':sum(c['smallest_capacity_bin'] is None for c in contexts)})
        all_tasks.extend(tasks);all_contexts.extend(contexts)
    for key in ('semantic_task_id','sample_id'):
        if len({t[key] for t in all_tasks})!=len(all_tasks):raise ValueError('duplicate '+key)
    groups={s:{t['source_group_id'] for t in all_tasks if t['split']==s} for s in ('train','eval')}
    if groups['train'] & groups['eval']:raise ValueError('source-group split overlap')
    report={'schema_version':'longworld.p64-codeforge-inventory.v1','status':'candidate_source_and_oracle_replayed','prior_qualified_inventory':{'path':str(prior_path.relative_to(ROOT)),'sha256':hashlib.sha256(prior_path.read_bytes()).hexdigest(),'repos':sorted(prior_repos)},'source_repository_snapshots':len(rows),'reused_frozen_source_collections':sum(not r['newly_fetched_source_collection_in_p64'] for r in rows),'newly_fetched_repository_source_collections':sum(r['newly_fetched_source_collection_in_p64'] for r in rows),'new_to_p62_qualified_repository_identities':sum(not r['previously_qualified_repo_in_p62'] for r in rows),'semantic_tasks':len(all_tasks),'samples':len(all_tasks),'variant_families':len({t['variant_family_id'] for t in all_tasks}),'split_counts':dict(Counter(t['split'] for t in all_tasks)),'unique_programs':len({t['program_id'] for t in all_tasks}),'context_capacity_counts':dict(Counter(c['smallest_capacity_bin'] for c in all_contexts if c['smallest_capacity_bin'])),'legacy_exact_band_context_counts':{b:sum(b in c['legacy_exact_bands'] for c in all_contexts) for b in ('64k','128k','256k')},'rows':rows,'limits':['Strict band counts measure complete source contexts; framework full-message token budgets remain a separate export check.','Capacity bins are upper bounds and do not claim contexts reach those lengths.','Overlapping whole-episode scopes share source records; task/sample/context counts are not independent world counts.','New source collection novelty is against the audited prior source inventory and P62 qualified map; compiler receipts do not independently certify global novelty.','No model/GPU training, production release, or legacy dataset union is certified.'],'production_eligible':False,'local_training_eligible':False}
    (ROOT/'reports/p64_codeforge_taskbank_inventory_20260909.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('source_repository_snapshots','semantic_tasks','split_counts','context_capacity_counts','legacy_exact_band_context_counts')},indent=2))


if __name__=='__main__':main()
