"""Bind final P64 inventory, source split and structural-holdout counts."""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def shape(value):
    if isinstance(value, dict):
        return {k: "FACT" if k == "fact_id" else ["RECORD"] * len(v) if k == "record_ids" else shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(v) for v in value]
    return value


def main():
    batch = ROOT / 'data/candidates/p64_finance_taskbank_v2'
    p63 = ROOT / 'data/candidates/p63_finance_taskbank_v1'
    finance = [r for p in sorted(batch.glob('*/tasks.jsonl')) for r in rows(p)]
    previous = {r['semantic_task_id'] for p in p63.glob('*/tasks.jsonl') for r in rows(p)}
    ids = {r['semantic_task_id'] for r in finance}
    groups, ops, shapes, families = [defaultdict(set) for _ in range(4)]
    for r in finance:
        split = r['split']
        groups[split].add(r['split_group_id'])
        ops[split].update(r['task_spec']['operators'])
        shapes[split].add(json.dumps(shape(r['task_spec']['program']), sort_keys=True))
        families[split].add(r['task_spec']['family'])
    assert not groups['train'] & groups['eval']
    report = {
        'schema_version': 'longworld.p64-dependency-inventory.v1',
        'finance': {
            'rows':len(finance), 'semantic_tasks':len(ids),
            'capacity_bins':dict(Counter(str(r['capacity_bin']) for r in finance)),
            'exact_context_numeric_ranges':dict(Counter(str(r['capacity_bin']) for r in finance if r['matches_exact_token_range'])),
            'min_context_tokens':min(r['context_tokens'] for r in finance),
            'max_context_tokens':max(r['context_tokens'] for r in finance),
            'unique_contexts':len({r['context_sha256'] for r in finance}),
            'split':dict(Counter(r['split'] for r in finance)),
            'source_groups':{k:sorted(v) for k,v in groups.items()},
            'source_groups_overlap':[],
            'p63_semantic_tasks_retained':len(ids & previous),
            'p63_semantic_tasks_not_in_p64':len(previous - ids),
            'new_semantic_tasks_relative_to_p63':len(ids - previous),
            'families':dict(Counter(r['task_spec']['family'] for r in finance)),
            'families_by_split':{s:dict(Counter(r['task_spec']['family'] for r in finance if r['split']==s)) for s in ['train','eval']},
            'primitive_operators_by_split':{k:sorted(v) for k,v in ops.items()},
            'eval_only_operators':sorted(ops['eval']-ops['train']),
            'eval_only_families':sorted(families['eval']-families['train']),
            'ast_shapes_by_split':{k:len(v) for k,v in shapes.items()},
            'eval_only_ast_shapes':len(shapes['eval']-shapes['train']),
            'ast_abstraction':'Replace fact_id and record_ids identities; preserve arity, roles, relations, methods and numeric operator constants.',
            'holdout_interpretation':'Program-family plus primitive-operator holdout; not pure composition generalization over known primitives.',
            'batch_receipt_sha256':digest(batch/'BATCH_RECEIPT.json'),
        },
    }
    stage = ROOT / 'data/candidates/p64_codeforge_sft_v1'
    meta = rows(stage/'metadata.jsonl')
    audit = {r['sample_id']:r for r in rows(ROOT/'reports/p64_codeforge_dependency_final/samples.jsonl')}
    selected = []
    for m in meta:
        r = audit[m['source_sample_id']]
        assert r['context_sha256'] == m['context_sha256']
        assert r['context_tokens'] == m['exact_context_tokens']
        if m['classification'] == 'long':
            selected.append(r)
    report['codeforge_primary'] = {
        'rows':len(selected), 'split':dict(Counter(r['split'] for r in selected)),
        'semantic_tasks':len({r['semantic_task_id'] for r in selected}),
        'contexts':len({r['context_id'] for r in selected}),
        'all_bound_records_and_scope_fit':{str(w):sum(r['bound_records_and_scope_windows'][str(w)]['all_bound_spans_fit'] for r in selected) for w in [4096,8192,16384]},
        'oracle_located_complete_heads_fit':{str(w):sum(r['oracle_located_complete_head_record_windows'][str(w)]['all_bound_spans_fit'] for r in selected) for w in [4096,8192,16384]},
        'exclusions':dict(Counter(m['classification'] for m in meta if m['classification']!='long')),
        'strict_verified':0,
        'limits':'Conservative whole-record geometry, not necessity. Head-only geometry omits scope and graph selection and does not establish a proof. No finance numeric reader was applied.',
        'metadata_sha256':digest(stage/'metadata.jsonl'),
        'dependency_summary_sha256':digest(ROOT/'reports/p64_codeforge_dependency_final/summary.json'),
    }
    output = ROOT/'reports/p64_dependency_inventory_final.json'
    if output.exists():
        raise ValueError('preserve existing report')
    report['script_sha256'] = digest(Path(__file__))
    output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
