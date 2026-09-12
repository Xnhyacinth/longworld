"""Exact natural scope capacity for the P65 GovInfo derivative source."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from longworld.core.attestation import sanitized_attestation_environment
from reports.p49_govinfo_bill_text_disposition_preflight import _unambiguous_map


def main():
    with sanitized_attestation_environment():
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained('Qwen/Qwen3.5-4B',revision='a7b0d22b993d71000cf2eadfb37222a67cee521e',local_files_only=True,trust_remote_code=False)
        reports=[]
        for path in sorted((ROOT/'data/source_inventory/p65_govinfo_derivatives_v1').glob('*.json')):
            world=json.loads(path.read_text())
            maps={s:_unambiguous_map(v['sections']) for s,v in world['stages'].items()}
            common=sorted(set(maps['eas'])&set(maps['eah']))
            prefixes=['all']+sorted({k.split('/')[0] for k in common if '/' in k})
            for prefix in prefixes:
                keys=[k for k in common if prefix=='all' or k.startswith(prefix+'/')]
                parts=[]; unique=[]; seen=set()
                for stage in ['eas','eah']:
                    for key in keys:
                        text=maps[stage][key]['text']
                        parts.append(f'\n=== {stage.upper()} | {key} ===\n{text}\n')
                        if text not in seen:
                            unique.append(text);seen.add(text)
                joined=''.join(parts)
                tokens=len(tokenizer.encode(joined,add_special_tokens=False))
                report={'bill_id':world['bill_id'],'scope':prefix,'section_pairs':len(keys),'context_tokens':tokens,'exact_unique_body_tokens':len(tokenizer.encode('\n'.join(unique),add_special_tokens=False)),'capacity_bin':next((c for c in [65536,131072,262144] if 32768<tokens<=c),None)}
                reports.append(report);print(report,flush=True)
    out=ROOT/'reports/p65_govinfo_capacity_probe.json'
    if out.exists():raise ValueError('preserve report')
    out.write_text(json.dumps(reports,indent=2)+'\n')


if __name__=='__main__':main()
