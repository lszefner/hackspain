from pathlib import Path
import json
from .core import *
from .interpretation import PROMPT_VERSION
from . import adapters

TRACKS={'ocr':'reading','isolated':'invoice','end_to_end':'invoice'}

def run_path(base,run_id):
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',run_id):raise ValueError('Unsafe run ID')
    return Path(base)/'runs'/run_id

def create_run(manifest_path,run_id,track,provider,model,split='development',reading_run=None,provisional=False,execution_metadata=None):
    mp=Path(manifest_path).resolve();m=manifest(mp);base=mp.parent;rd=run_path(base,run_id)
    if track=='end_to_end' and not reading_run:raise ValueError('End-to-end needs --reading-run')
    if track!='end_to_end' and reading_run:raise ValueError('Only end-to-end accepts --reading-run')
    if track=='ocr' and provider not in ['fal','import']:raise ValueError('OCR provider must be fal or import')
    if track!='ocr' and provider not in ['jev','deepseek','helmcode','import']:raise ValueError('Invalid interpretation provider')
    entries=[d for d in m['documents'] if split=='all' or d['split']==split]
    config=dict(track=track,provider=provider,model=model,split=split,reading_run=reading_run,provisional=provisional,
        prompt_version=PROMPT_VERSION,code_hash=value_hash({n:file_hash(ROOT/n) for n in ['runner.py','adapters.py','interpretation.py']}),
        schemas_hash=value_hash({p.name:file_hash(p) for p in (ROOT/'schemas').glob('*.json')}))
    if execution_metadata is not None:
        if provider!='import':raise ValueError('External execution metadata is only for imports')
        config['external_execution']=execution_metadata
        config['prompt_version']=execution_metadata['prompt_version']
    r=dict(schema_version='0.1',run_id=run_id,track=track,provider=provider,model=model,prompt_version=config['prompt_version'],config_hash=value_hash(config),
        manifest_hash=file_hash(mp),split=split,reading_run=reading_run,provisional=provisional,created_at=now(),config=config,documents=[d['file_id'] for d in entries])
    if (rd/'run.json').exists():
        old=validate('run',load(rd/'run.json'))
        if old['config_hash']!=r['config_hash'] or old['manifest_hash']!=r['manifest_hash']:raise ValueError('Run configuration changed; choose a new run ID')
        return old
    validate('run',r);dump(rd/'run.json',r);dump(rd/'manifest.json',m)
    for e in entries:dump(rd/key(e['file_id'])/'record.json',record(r,e))
    return r

def record(run,entry):
    return dict(schema_version='0.1',file_id=entry['file_id'],source_sha256=entry['sha256'],reading_sha256=None,status='pending',
        provider=run['provider'],model=run['model'],resolved_model=None,prompt_version=run['prompt_version'],latency_seconds=None,usage={},cost_usd=None,attempts=0,error=None,artifact_sha256=None,imported=False)

def get_run(mp,run_id):
    mp=Path(mp).resolve();m=manifest(mp);rd=run_path(mp.parent,run_id);r=validate('run',load(rd/'run.json'))
    if r['manifest_hash']!=file_hash(mp):raise ValueError('Manifest changed since run creation')
    return m,r,rd

def reading_source(base,run,entry):
    if run['track']=='isolated':
        # Deliberately never load the stage3 reference in execution code.
        pp=paths(base,entry['file_id']);p=validate('provenance',load(pp['provenance']));reading=validate('reading',load(pp['stage2']))
        meta=p['stage2']
        if p['source_sha256']!=entry['sha256'] or meta['artifact_sha256']!=file_hash(pp['stage2']):raise ValueError('Reading identity/hash mismatch')
        if not meta['complete']:raise ValueError('Incomplete reading reference')
        if meta['status']!='human_verified' and not run['provisional']:raise ValueError('Reading unreviewed: explicit --provisional run required')
        return reading
    if run['track']=='end_to_end':
        rr=run_path(base,run['reading_run']);source_run=validate('run',load(rr/'run.json'))
        if source_run['track']!='ocr':raise ValueError('Reading run must be OCR')
        d=rr/key(entry['file_id']);record=validate('record',load(d/'record.json'))
        if record['status']!='success' or record['source_sha256']!=entry['sha256']:raise ValueError('Upstream OCR unavailable or source mismatch')
        if record['artifact_sha256']!=file_hash(d/'output.json'):raise ValueError('OCR artifact hash changed')
        return validate('reading',load(d/'output.json'))
    return None

def execute(mp,run_id,retry_failed=False,max_attempts=2,limit=None):
    if not 1<=max_attempts<=5:raise ValueError('max-attempts must be 1..5')
    m,r,rd=get_run(mp,run_id);base=Path(mp).resolve().parent;count=0
    if r['config']['code_hash']!=value_hash({n:file_hash(ROOT/n) for n in ['runner.py','adapters.py','interpretation.py']}):raise ValueError('Execution code changed; use a new run ID. Stored outputs remain rescorable.')
    if r['provider']=='import':raise ValueError('Use import-output for an import run')
    for e in m['documents']:
        if e['file_id'] not in r['documents']:continue
        folder=rd/key(e['file_id']);rec=validate('record',load(folder/'record.json'))
        if rec['status']=='success':
            if rec['artifact_sha256']!=file_hash(folder/'output.json'):raise ValueError('Cached output was modified')
            continue
        if rec['status']!='pending' and not retry_failed:continue
        if limit is not None and count>=limit:break
        count+=1;caller=adapters.Caller(folder/'raw',max_attempts)
        try:
            reading=reading_source(base,r,e)
            if reading is not None:
                dump(folder/'input-reading.json',reading);rec['reading_sha256']=file_hash(folder/'input-reading.json')
            dump(folder/'record.json',rec)
            if r['track']=='ocr':output,usage=adapters.fal(base/e['path'],e['file_id'],r['model'],caller)
            elif r['provider']=='jev':output=adapters.jev(reading,r['model'],caller);usage={}
            else:output=adapters.deepseek(reading,r['model'],r['provider'],caller);usage={}
            dump(folder/'output.json',output)
            validate(TRACKS[r['track']],output)
            if output['file_id']!=e['file_id']:raise ValueError('Output file_id mismatch')
            rec.update(status='success',error=None,artifact_sha256=file_hash(folder/'output.json'))
            rec.update(caller.totals());rec['usage'].update(usage)
        except (ValueError,KeyError,TypeError,IndexError) as ex:
            rec.update(status='invalid',error=type(ex).__name__+': '+adapters.redact(str(ex)));rec.update(caller.totals())
        except (RuntimeError,OSError) as ex:
            rec.update(status='failed',error=type(ex).__name__+': '+adapters.redact(str(ex)));rec.update(caller.totals())
        validate('record',rec);dump(folder/'record.json',rec)
        print(e['file_id']+': '+rec['status'],flush=True)

def import_output(mp,run_id,file_id,output_path,raw_request,raw_response,metadata_path):
    m,r,rd=get_run(mp,run_id);base=Path(mp).resolve().parent
    if file_id not in r['documents']:raise ValueError('File not in this run')
    e=next(d for d in m['documents'] if d['file_id']==file_id);folder=rd/key(file_id)
    old=load(folder/'record.json')
    if old['status']=='success':raise ValueError('Completed import immutable; use a new run')
    meta=load(metadata_path);rec=record(r,e);rec['imported']=True
    for k in ['latency_seconds','usage','cost_usd','attempts','resolved_model']:
        if k not in meta:raise ValueError('Import metadata missing '+k)
        rec[k]=meta[k]
    # External provenance is an attestation, not independently verifiable provider evidence.
    if meta.get('source_sha256')!=e['sha256']:raise ValueError('Import source hash required and must match')
    reading=reading_source(base,r,e)
    if reading is not None:
        dump(folder/'input-reading.json',reading);rec['reading_sha256']=file_hash(folder/'input-reading.json')
        if meta.get('reading_sha256')!=rec['reading_sha256']:raise ValueError('Import reading hash mismatch')
    for src,name in [(raw_request,'request.txt'),(raw_response,'response.txt')]:adapters.safe_write(folder/'raw/import'/name,Path(src).read_text())
    try:
        output=load(output_path);dump(folder/'output.json',output);validate(TRACKS[r['track']],output)
        if output['file_id']!=file_id:raise ValueError('Output file identity mismatch')
        rec.update(status='success',artifact_sha256=file_hash(folder/'output.json'))
    except (ValueError,TypeError) as ex:rec.update(status='invalid',error=type(ex).__name__)
    validate('record',rec);dump(folder/'record.json',rec)
