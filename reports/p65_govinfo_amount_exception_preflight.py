"""Bounded GovInfo source/grammar preflight; original XML remains in memory."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reports import p49_govinfo_bill_text_disposition_preflight as source

OUTPUT=ROOT/'data/source_inventory/p65_govinfo_derivatives_v1'
REPORT=ROOT/'reports/p65_govinfo_amount_exception_preflight.json'
MONEY=re.compile(r'\$\s*(\d[\d,]*(?:\.\d+)?)')
EXCEPTION=re.compile(r'\b(?:provided(?: further)?[, ]+that|except(?: that| as)?|notwithstanding|shall not)\b',re.I)


def work(chain,config):
    status_raw,status_receipt=source._fetch(chain['status_source'])
    status=source._validate_status(source._xml(status_raw,'status'),chain)
    stages={}
    receipts=[status_receipt]
    for stage in chain['stages']:
        if stage['stage'] not in ['eas','eah']:
            continue
        raw,receipt=source._fetch(stage)
        root=source._xml(raw,stage['stage'])
        source._validate_stage(root,chain,stage,config['public_domain_text'])
        sections,ambiguous=source._section_units(root,set(config['section_oracle']['structural_ancestors']),set(config['section_oracle']['presentation_only_tags']))
        stages[stage['stage']]={'date':stage['date'],'url':stage['url'],'source_sha256':receipt['sha256'],'sections':sections,'ambiguous_keys':ambiguous}
        receipts.append(receipt)
    maps={s:source._unambiguous_map(v['sections']) for s,v in stages.items()}
    common=sorted(set(maps['eas']) & set(maps['eah']))
    rows=[]
    for key in common:
        old,new=maps['eas'][key]['text'],maps['eah'][key]['text']
        a,b=MONEY.findall(old),MONEY.findall(new)
        if len(a)==len(b)==1:
            rows.append({'key':key,'old':a[0],'new':b[0],'old_exception':bool(EXCEPTION.search(old)),'new_exception':bool(EXCEPTION.search(new)),'changed':a[0]!=b[0],'old_chars':len(old),'new_chars':len(new)})
    world={'schema_version':'longworld.p65-govinfo-derived-source.v1','bill_id':chain['bill_id'],'stages':stages,'status_receipt':status,'sources':receipts,'public_domain_statement':config['public_domain_text'],'source_config_sha256':hashlib.sha256((ROOT/'configs/p49_govinfo_bill_text_disposition_preflight_v1.json').read_bytes()).hexdigest(),'raw_xml_persisted':False,'legal_effect_claimed':False,'production_eligible':False}
    return world,{'bill_id':chain['bill_id'],'stage_sections':{s:len(v['sections']) for s,v in stages.items()},'common_unambiguous_sections':len(common),'one_amount_pairs':len(rows),'changed_amount_pairs':sum(r['changed'] for r in rows),'changed_with_exception':sum(r['changed'] and (r['old_exception'] or r['new_exception']) for r in rows),'exception_membership_changed':sum(r['old_exception']!=r['new_exception'] for r in rows),'eligible_rows':rows,'structural_prefixes':dict(Counter(k.split('/')[0] for k in common))}


def main():
    if OUTPUT.exists() or REPORT.exists():
        raise ValueError('preserve existing P65 source outputs')
    config=json.loads((ROOT/'configs/p49_govinfo_bill_text_disposition_preflight_v1.json').read_text())
    results=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for world,receipt in pool.map(lambda chain:work(chain,config),config['chains']):
            OUTPUT.mkdir(parents=True,exist_ok=True)
            path=OUTPUT/(world['bill_id']+'.json')
            path.write_text(json.dumps(world,ensure_ascii=False,sort_keys=True)+'\n')
            receipt['derived_world_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            results.append(receipt)
            print(json.dumps({k:v for k,v in receipt.items() if k!='eligible_rows'},indent=2),flush=True)
    REPORT.write_text(json.dumps({'schema_version':'longworld.p65-govinfo-amount-exception-preflight.v1','worlds':results,'source_entities':2,'net_new_source_entities':0,'strict_verified':0},indent=2,sort_keys=True)+'\n')


if __name__=='__main__':
    main()
