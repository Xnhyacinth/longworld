"""Replay task answers from reader-visible fields only; retain a compact receipt."""
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from longworld.core.codeforge_taskbank import render_context, evaluate_program


def main():
    rows=[]
    catalog=json.loads((ROOT/'configs/p64_codeforge_taskbank_catalog_v1.json').read_text())
    for job in catalog['jobs']:
        base=ROOT/job['output']; world=json.loads((base/'world.json').read_text())
        tasks=[json.loads(line) for line in (base/'tasks.jsonl').read_text().splitlines()]
        cache={}; checked=0
        for task in tasks:
            context_id=task['context_id']
            if context_id not in cache:
                rendered=json.loads(render_context(world,task['parameters']['episode_ids']))
                visible={'records':[dict(r,record_id=r['id'],original_id=r['id']) for r in rendered['records']], 'episodes':[]}
                ids=[]
                for i,scope in enumerate(rendered['scope']):
                    eid='visible_episode_'+str(i);ids.append(eid)
                    visible['episodes'].append({'episode_id':eid,'record_ids':scope['records'],'record_map':{k:k for k in scope['records']}})
                cache[context_id]=(visible,ids)
            visible,ids=cache[context_id]
            result=evaluate_program(visible,ids,task['program_id'])
            if result['answer']!=task['oracle_answer']:
                raise ValueError('hidden-field dependence: '+task['sample_id'])
            checked+=1
        row={'repository':job['repository'],'checked_tasks':checked,'checked_contexts':len(cache),'rendered_only_answer_matches':True}
        rows.append(row);print(json.dumps(row),flush=True)
    (ROOT/'reports/p64_codeforge_visible_replay_receipt.json').write_text(json.dumps({'status':'PASS','scope':'same finite oracle replayed on only compact reader fields; not an independent scientific quality audit','repositories':rows},indent=2)+'\n')


if __name__=='__main__':main()
