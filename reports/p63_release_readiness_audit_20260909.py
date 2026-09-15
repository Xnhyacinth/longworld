"""Read current bound legacy inventory and frozen financial-source sizes."""
from pathlib import Path
from collections import Counter
from html.parser import HTMLParser
import hashlib,json,re,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from longworld.core.p57pipeline import question_only_codebook_prediction
from longworld.core.release_inventory import PRODUCTION_PACKAGE_READY_PROFILE_IDS
from longworld.core.release_profile import ISSUABLE_PRODUCTION_PROFILE_IDS

class TextCounter(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.skip=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','ix:hidden'}:self.skip+=1
    def handle_endtag(self,tag):
        if tag in {'script','style','ix:hidden'} and self.skip:self.skip-=1
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)

def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
    previous=json.loads((ROOT/'reports/p60_reading_readiness_audit_20260908_v2.json').read_text())
    rows=[];checks=[];b5_n=0;b5_tokens=0
    for product in previous['products']:
        base=Path(product['path']);gate=json.loads((base/'release_gate_receipt.json').read_text());ok={name:sha((base/name).read_bytes())==digest for name,digest in gate['source_file_sha256'].items()}
        manifest=json.loads((base/'llamafactory/training_export_manifest.json').read_text())
        ok.update({entry['path']:sha((base/entry['path']).read_bytes())==entry['sha256'] for entry in manifest['outputs']})
        meta=json.loads((base/'llamafactory/B5.meta.json').read_text());b5_n+=meta['n'];b5_tokens+=meta['tokens_est'];checks.append({'product':base.name,'gate_file_checks':ok,'gate_ok':gate['ok'],'production_eligible':gate['production_eligible']})
        for split in ('train','eval'):
            for number,line in enumerate((base/(split+'.jsonl')).read_text().splitlines(),1):
                row=json.loads(line);flags=[];question=row.get('question','');context=row.get('document_context',row.get('context',''));answer=row.get('answer','');visible=question+'\n'+context
                pred=question_only_codebook_prediction(question)
                try:gold=json.loads(answer)
                except (ValueError,TypeError):gold=None
                if pred is not None and pred==gold:flags.append('known_question_only')
                digests=re.findall(r'(?:^|;)patch=([a-f0-9]{64})(?:;|$)',answer)
                if any(value not in visible for value in digests):flags.append('needs_byte_hash_tool')
                ancestry=[value for pair in re.findall(r'ancestry=([a-f0-9]{40})->([a-f0-9]{40})',answer) for value in pair]
                if any(value not in visible for value in ancestry):flags.append('needs_source_id_evidence')
                row['_product']=base.name;row['_split']=split;row['_number']=number;row['_line_sha256']=sha(line.encode());row['_flags']=flags;rows.append(row)
    groups={(r['world_id'],r.get('answer_program_id')) for r in rows if r['_flags']}
    holds=[r for r in rows if (r['world_id'],r.get('answer_program_id')) in groups]
    prior_bindings=[]
    lookup={(r['_product'],r['_split'],r['_number']):r for r in rows}
    for split,held in previous['hold_rows_by_split'].items():
        for entry in held:
            r=lookup[(entry['product'],split,entry['row_number'])];prior_bindings.append(r['_line_sha256']==entry['row_line_sha256'])
    legacy={'products':len(checks),'counts':{s:{'rows':sum(r['_split']==s for r in rows),'exact_recorded_context_tokens':sum(r['tokenizer_context_tokens'] for r in rows if r['_split']==s)} for s in ('train','eval')},'world_ids':len({r['world_id'] for r in rows}),'identifier_counts_not_independence':{k:len({r[k] for r in rows if r.get(k)}) for k in ('base_task_id','semantic_base_task_id','answer_program_id','executable_proof_id')},'direct_counterexamples':dict(Counter(r['_split'] for r in rows if r['_flags'])),'propagated_family_holds':dict(Counter(r['_split'] for r in holds)),'finance_direct_or_family_holds':sum(r.get('domain')=='finance' for r in holds),'unheld_counts_not_clean':{s:sum(r['_split']==s for r in rows)-sum(r['_split']==s for r in holds) for s in ('train','eval')},'old_hold_row_bytes_match':all(prior_bindings),'all_gate_export_hashes_match':all(all(c['gate_file_checks'].values()) for c in checks),'b5':{'n':b5_n,'estimated_tokens':b5_tokens},'production_rows':sum(bool(r.get('production_eligible')) for r in rows),'eval_world_ids':sorted({r['world_id'] for r in rows if r['_split']=='eval'}),'checks':checks}
    catalog=json.loads((ROOT/'configs/p63_finance_taskbank_source_catalog_v1.json').read_text());paths={ROOT/entry['source_manifest'] for entry in catalog['jobs']}
    paths.update((ROOT/'data/source_inventory').glob('*finance*/*manifest*signed.json'))
    paths.update((ROOT/'data/source_inventory').glob('p14_company_*/*annual_report_inventory.signed.json'))
    sources=[]
    for p in sorted(paths):
        d=json.loads(p.read_text());records=d.get('records',d.get('filings',[]));details=[]
        for r in records:
            text=r.get('text');blob=None
            if text is None and r.get('text_file'):
                f=p.parent/r['text_file'];blob=f.read_bytes();text=blob.decode('utf-8')
            if text is None:continue
            blob=text.encode('utf-8') if blob is None else blob
            html='<' in text[:300];parser=TextCounter()
            if html:parser.feed(text);plain=' '.join(' '.join(parser.parts).split())
            else:plain=text
            facts=r.get('derived_facts',[]);facts=facts if isinstance(facts,list) else []
            numeric=[f for f in facts if isinstance(f,dict) and 'numeric_value' in f]
            quote_checks=[text[f['evidence_char_start']:f['evidence_char_start']+len(f['evidence_quote'])]==f['evidence_quote'] for f in numeric if 'evidence_char_start' in f and 'evidence_quote' in f]
            details.append({'record_id':r.get('record_id'),'report_date':r.get('report_date',r.get('year')),'text_utf8_bytes':len(blob),'literal_text_chars':len(text),'plain_text_chars_estimate':len(plain),'raw_text_format':'html' if html else 'plain_text','text_sha256':sha(blob),'declared_text_hash_matches':sha(blob)==r.get('text_sha256',r.get('source_sha256')),'verified_numeric_role_count':len(numeric),'numeric_roles':sorted({f['field'] for f in numeric}),'numeric_quote_offsets_match':all(quote_checks),'numeric_role_offset_checks':len(quote_checks)})
        schema=d.get('schema_version');issuer=d.get('issuer',{});sources.append({'manifest':str(p.relative_to(ROOT)),'sha256':sha(p.read_bytes()),'schema':schema,'issuer':issuer,'direct_taskbank_schema':schema in {'longworld.issuer-ir-filing-manifest.v1','longworld.issuer-inline-xbrl-manifest.v1'},'record_count':len(details),'raw_text_bytes_sum':sum(r['text_utf8_bytes'] for r in details),'plain_text_chars_estimate_sum':sum(r['plain_text_chars_estimate'] for r in details),'unique_text_hashes':len({r['text_sha256'] for r in details}),'explicit_license_fields_found':sorted({str(r.get('license')) for r in records if r.get('license')}|({str(d['license'])} if d.get('license') else set())),'attestation_environment':d.get('attestation',{}).get('environment'),'source_authorization_record':d.get('authorization',{}).get('record_id'),'records':details})
    out={'schema_version':'longworld.p63-release-readiness-audit.v1','legacy_current_bytes':legacy,'finance_sources':sources,'production_profile_ids':sorted(ISSUABLE_PRODUCTION_PROFILE_IDS),'production_package_ready_ids':sorted(PRODUCTION_PACKAGE_READY_PROFILE_IDS),'limits':['Fresh SHA256 and finite codebook/hash/ancestry detector replay only; no HMAC revalidation or retokenization of old row token counts.','Unheld rows are not certified clean; source rights are not established by public access or local HMAC.','HTML plain-text character counts are a size estimate, not exact tokenizer capacity or proof-bearing content.','Berkshire PDF schema is not directly compatible with the current IR/inline numeric-role compiler.']}
    (ROOT/'reports/p63_release_readiness_audit_20260909.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({k:v for k,v in legacy.items() if k!='checks'},indent=2))
    print('source manifests',len(sources))
if __name__=='__main__':main()
