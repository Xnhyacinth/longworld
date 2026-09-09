"""Replay the gold-blind reader on its label-selected raw table prefix."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from longworld.core.attestation import attestation_environment_names
from longworld.core.finance_visible_reader import read_latest_filing
from longworld.core.tokenizer_assets import resolved_tokenizer_asset_manifest_sha256


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    model='Qwen/Qwen3.5-4B'
    revision='a7b0d22b993d71000cf2eadfb37222a67cee521e'
    for name in attestation_environment_names():
        os.environ.pop(name,None)
    os.environ['TOKENIZERS_PARALLELISM']='false'
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(model,revision=revision,local_files_only=True,trust_remote_code=False)
    assets=resolved_tokenizer_asset_manifest_sha256(model,revision)
    source=ROOT/'reports/p64_finance_visible_reader_v1/samples.jsonl'
    records=[json.loads(line) for line in source.read_text().splitlines()]
    tasks={}
    for file in (ROOT/'data/candidates/p64_finance_taskbank_v2').glob('*/tasks.jsonl'):
        for row in map(json.loads,file.read_text().splitlines()):
            tasks[row['sample_id']]=(file.parent,row)
    measures=[]
    for record in records:
        if record['prediction']['status']!='predicted':
            continue
        base,row=tasks[record['sample_id']]
        context=(base/row['context_path']).read_text()
        headers=list(re.finditer(r'(?m)^=== Annual filing: .+? ===$',context))
        latest=context[headers[-1].start():]
        table=record['prediction']['table']
        start,end=table['start'],max(r['end'] for r in table['rows'])
        crop=latest[start:end]
        # The crop boundary comes from visible labels, never gold cell locations.
        prediction=read_latest_filing(row['task_spec']['question'],crop)
        if prediction['status']!='predicted' or prediction['answer']!=record['prediction']['answer']:
            raise ValueError('local visible table does not reproduce full-filing reader')
        measures.append({'sample_id':record['sample_id'],'semantic_task_id':record['semantic_task_id'],
                         'crop_start_in_latest_filing':start,'crop_end_in_latest_filing':end,
                         'crop_sha256':hashlib.sha256(crop.encode()).hexdigest(),
                         'table_tokens':len(tokenizer.encode(crop,add_special_tokens=False)),
                         'table_plus_question_tokens':len(tokenizer.encode(crop+'\n\nQuestion:\n'+row['task_spec']['question'],add_special_tokens=False)),
                         'prediction_preserved':True,'answer_exact_match':prediction['answer']==row['task_spec']['answer'],
                         'scope_status':prediction['scope_status']})
    out=source.parent/'local_table_replay.json'
    if out.exists():
        raise ValueError('preserve existing report')
    summary={'schema_version':'longworld.visible-cashflow-local-table-replay.v1',
             'predictions':len(measures),'exact_matches':sum(m['answer_exact_match'] for m in measures),
             'min_table_tokens':min(m['table_tokens'] for m in measures),'max_table_tokens':max(m['table_tokens'] for m in measures),
             'max_table_plus_question_tokens':max(m['table_plus_question_tokens'] for m in measures),
             'tokenizer':{'model_id':model,'revision':revision,'asset_manifest_sha256':assets},
             'source_predictions_sha256':digest(source),'reader_code_sha256':digest(ROOT/'longworld/core/finance_visible_reader.py'),
             'script_sha256':digest(Path(__file__)),'samples':measures,
             'limits':'Gold-blind label-selected raw substring, independently retokenized. Latest-comparative scope mismatch retained; not a strict proof or neural score.'}
    out.write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='samples'},indent=2))


if __name__=='__main__':
    main()
