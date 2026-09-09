"""Gold-blind latest-visible-table predictions; gold used only after inference."""
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from longworld.core.finance_visible_reader import REVISION, read_latest_filing


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    batch = ROOT/'data/candidates/p64_finance_taskbank_v2'
    out = ROOT/'reports/p64_finance_visible_reader_v1'
    if out.exists():
        raise ValueError('preserve old results')
    results = []
    contexts = {}
    by_issuer = defaultdict(Counter)
    inputs = {}
    for taskfile in sorted(batch.glob('*/tasks.jsonl')):
        inputs[str(taskfile.relative_to(ROOT))] = digest(taskfile)
        for row in map(json.loads, taskfile.read_text().splitlines()):
            path = taskfile.parent/row['context_path']
            if path not in contexts:
                context = path.read_text()
                assert hashlib.sha256(context.encode()).hexdigest() == row['context_sha256']
                headers = list(re.finditer(r'(?m)^=== Annual filing: .+? ===$', context))
                assert headers
                contexts[path] = context[headers[-1].start():]
            latest = contexts[path]
            # Only the public question and visible latest filing cross the reader API.
            question = row['task_spec']['question']
            prediction = read_latest_filing(question, latest)
            # Oracle data are consulted only after the prediction is fixed.
            gold = row['task_spec']['answer']
            matched = prediction['status']=='predicted' and prediction['answer']==gold
            result = {
                'sample_id':row['sample_id'], 'semantic_task_id':row['semantic_task_id'],
                'issuer':taskfile.parent.name, 'family':row['task_spec']['family'],
                'split':row['split'], 'required_filings':row['source_document_count'],
                'context_sha256':row['context_sha256'],
                'reader_input_sha256':hashlib.sha256(json.dumps([question,latest],ensure_ascii=False).encode()).hexdigest(),
                'latest_visible_filing_sha256':hashlib.sha256(latest.encode()).hexdigest(),
                'prediction':prediction, 'answer_exact_match':matched,
                'valid_original_filing_proof':False,
            }
            results.append(result)
            count = by_issuer[taskfile.parent.name]
            count['total'] += 1
            count[prediction['status']] += 1
            count['exact_match'] += matched
            count['multi_filing_exact_match'] += matched and row['source_document_count']>1
    out.mkdir()
    samples = out/'samples.jsonl'
    samples.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in results))
    predicted = [r for r in results if r['prediction']['status']=='predicted']
    summary = {
        'schema_version':REVISION, 'input_contract':'Only public question and visible latest required filing; no gold values, source attributes, answer-cell offsets or fact IDs.',
        'total':len(results),'predicted':len(predicted),'unsupported':len(results)-len(predicted),
        'exact_match':sum(r['answer_exact_match'] for r in predicted),
        'multi_filing_predicted':sum(r['required_filings']>1 for r in predicted),
        'multi_filing_exact_match':sum(r['answer_exact_match'] and r['required_filings']>1 for r in predicted),
        'single_filing_exact_match':sum(r['answer_exact_match'] and r['required_filings']==1 for r in predicted),
        'unsupported_reasons':dict(Counter(r['prediction']['reason'] for r in results if r['prediction']['status']=='unsupported')),
        'by_issuer':{k:dict(v) for k,v in by_issuer.items()},
        'predicted_by_family':dict(Counter(r['family'] for r in predicted)),
        'mismatches_by_family':dict(Counter(r['family'] for r in predicted if not r['answer_exact_match'])),
        'scope':'Latest comparatives can match the target answer without proving as-reported-original scope. Every multi-filing prediction is explicitly scope-mismatched; no strict labels changed.',
        'strict_proofs':0,'neural_baseline':'unmeasured',
        'task_file_sha256':inputs,'samples_sha256':digest(samples),
        'code_sha256':{str(p.relative_to(ROOT)):digest(p) for p in [Path(__file__),ROOT/'longworld/core/finance_visible_reader.py',ROOT/'longworld/core/finance_taskbank.py']},
    }
    (out/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
