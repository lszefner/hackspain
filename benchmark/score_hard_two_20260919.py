import hashlib,json,sys
from pathlib import Path
from datetime import datetime
from collections import Counter
sys.path.insert(0,str(Path.cwd()))
from benchmark.core import reference,file_hash
from benchmark.scoring import score_invoice,score_reading,summarize_fields
root=Path('benchmark/runs/hard-two-20260919'); export=root/'export'
manifest=json.loads((root/'selection.json').read_text())
entries={e['file_id']:e for e in manifest['documents']}
outcomes=[json.loads(l) for l in (export/'outcomes.jsonl').read_text().splitlines()]
assert len(outcomes)==2 and {o['file_id'] for o in outcomes}==set(entries)
events=[json.loads(l) for l in (root/'events.jsonl').read_text().splitlines()]
report={'batch_id':'42f78b82-dca2-46ce-85c9-232ecd90215b','method':'Fresh current product OCR + Jev; existing deterministic scorers called directly because full-run importer requires exactly 50 inputs. Fixed draft references; uncertain reference fields excluded. No tuning or reference edits.','wall_time':json.loads((root/'wall-time.json').read_text()),'documents':[]}
for o in outcomes:
 fid=o['file_id']; entry=entries[fid]; assert file_hash(entry['path'])==entry['sha256']
 def artifact(name):
  path=export/o['artifacts'][name]; assert file_hash(path)==o['artifact_hashes'][name]; return json.loads(path.read_text())
 reading,invoice,prov=reference(Path('benchmark'),entry)
 pred_reading=artifact('reading'); pred_invoice=artifact('invoice'); raw=artifact('raw')
 score=score_invoice(invoice,pred_invoice,prov['stage3']['excluded_pointers'])
 ocr=score_reading(reading,pred_reading,prov['stage2']['excluded_pointers'])
 # Named core facts, excluding uncertain fields and annotations; nulls earn no credit.
 core=[d for d in score['details'] if d['reference'] is not None and not d['field'].startswith(('/annotations/','/additional_fields/'))]
 ref_lines=len(invoice['lines']); ref_taxes=len(invoice['taxes'])
 # Stage events match the interpretation duration exported with this outcome.
 completed=[e for e in events if e.get('event')=='attempt_completed' and e.get('stage')=='interpretation' and abs(e['latency_seconds']-o['latency_seconds'])<.001]
 assert len(completed)==1
 end=completed[0]; local=[e for e in events if e.get('input_id')==end['input_id']]
 attempt_ids={e['attempt_id'] for e in local if 'attempt_id' in e}
 calls=[e for e in events if e.get('event')=='provider_request_completed' and e['attempt_id'] in attempt_ids]
 stages={e['stage']:e['latency_seconds'] for e in local if e['event']=='attempt_completed'}
 starts=[datetime.fromisoformat(e['time']) for e in local if e['event']=='attempt_started' and e['stage']=='reading']
 item={'file_id':fid,'runtime_status':o['status'],'invoice':pred_invoice,'reading':pred_reading,'checks':artifact('checks'),'coverage':artifact('coverage'),'invoice_score':score,'ocr_score':ocr,'core_facts':summarize_fields(core),'operations':{'stage_seconds':stages,'reading_start_to_interpretation_finish_seconds':(datetime.fromisoformat(end['time'])-min(starts)).total_seconds(),'http_calls':dict(Counter(e['provider']+' '+e['method'] for e in calls)),'usage':o['usage'],'resolved_model':o.get('resolved_model'),'cost_usd':o['cost_usd'],'attempts':o['attempts']},'reference_hashes':{s:prov[s]['artifact_sha256'] for s in ['stage2','stage3']}}
 report['documents'].append(item)
report['aggregate_fields']=summarize_fields([d for o in report['documents'] for d in o['invoice_score']['details']])
Path('benchmark/reports/product-hard-two-20260919.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
for d in report['documents']:
 print(d['file_id'],json.dumps({'fields':d['invoice_score']['fields'],'core':d['core_facts'],'ocr':d['ocr_score']['whitespace_normalized'],'operations':d['operations']},ensure_ascii=False))
