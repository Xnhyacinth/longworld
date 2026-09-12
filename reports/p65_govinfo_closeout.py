"""Current P65 GovInfo accounting and narrow baseline receipts."""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    directory=ROOT/'data/candidates/p65_govinfo_taskbank_v2'
    taskpath=directory/'tasks.jsonl'
    rows=[json.loads(line) for line in taskpath.read_text().splitlines()]
    long=[r for r in rows if r['classification']=='long']
    summary={
        'schema_version':'longworld.p65-govinfo-closeout.v1',
        'status':'live_source_and_full_artifact_replay_passed',
        'eligible_source_chains':1,'probed_existing_source_chains':2,'net_new_source_entities':0,
        'semantic_tasks':len({r['semantic_task_id'] for r in rows}),'training_views':len(rows),
        'distinct_structured_answers':len({json.dumps(r['answer'],sort_keys=True) for r in rows}),
        'long_rows':len(long),'short_rows':sum(r['classification']=='short' for r in rows),
        'contexts':len({r['context_sha256'] for r in rows}),
        'long_context_tokens':sorted({r['context_tokens'] for r in long}),
        'long_context_capacity_bins':dict(Counter(str(r['capacity_bin']) for r in long)),
        'long_full_message_capacity_bins':dict(Counter(str(next(c for c in [65536,131072,262144] if r['full_hf_chat_tokens']<=c)) for r in long)),
        'long_exact_context_numeric_ranges':dict(Counter(r['exact_numeric_range'] for r in long if r['exact_numeric_range'])),
        'max_full_chat_tokens':max(r['full_hf_chat_tokens'] for r in rows),
        'profiles':dict(Counter(r['profile'] for r in rows)),
        'long_profiles':dict(Counter(r['profile'] for r in long)),
        'whole_record_window_answer_em':{str(w):{'all_rows':sum(r['window_probe'][str(w)]['answer_em'] for r in rows),'long_rows':sum(r['window_probe'][str(w)]['answer_em'] for r in long)} for w in [4096,8192,16384]},
        'latest_print_only_answer_em':sum(r['latest_print_only_answer_em'] for r in rows),
        'earliest_print_only_answer_em':sum(r['earliest_print_only_answer_em'] for r in rows),
        'empty_context_constant_answer_em':sum(r['empty_context_constant_answer_em'] for r in rows),
        'strict_verified':0,'neural_baselines':'unmeasured',
        'limitations':'Window/single-print EM is a finite complete-record probe and does not prove full population scope or exhaustive alternative-proof failure. Source verification is official hash-pinned replay, not HMAC/production attestation.',
        'source_config':'configs/p65_govinfo_taskbank_hr4366_v1.json',
        'source_world':'data/source_inventory/p65_govinfo_derivatives_v1/118-HR-4366.json',
        'build_receipt_sha256':digest(directory/'BUILD_RECEIPT.json'),'tasks_sha256':digest(taskpath),
        'script_sha256':digest(Path(__file__)),
        'hr815_result':'zero paired-single-amount tasks in the bounded probe; monetary source population 12722 tokens, preserved as short/grammar diagnostic and not exported',
    }
    out=ROOT/'reports/p65_govinfo_closeout.json'
    if out.exists():raise ValueError('preserve prior report')
    out.write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
