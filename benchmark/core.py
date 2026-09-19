from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent

def load(path):
    return json.loads(Path(path).read_text())

def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    os.replace(tmp, path)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_hash(path): return digest(Path(path).read_bytes())
def value_hash(value): return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())
def now(): return datetime.now(timezone.utc).isoformat()
def key(file_id): return digest(file_id.encode())[:16]
def schema(name): return load(ROOT / 'schemas' / f'{name}.json')
def validate(name, value):
    errors = list(Draft202012Validator(schema(name), format_checker=FormatChecker()).iter_errors(value))
    if errors:
        # Do not include untrusted values (which could contain secrets) in errors.
        raise ValueError('; '.join('/' + '/'.join(map(str,e.path)) + ': ' + e.validator for e in errors[:20]))
    if name == 'reading':
        ids=[]
        for p in value['pages']:
            for b in p['blocks']:
                ids.append(b['id'])
                if b['kind'] == 'table':
                    if b['text']: raise ValueError('Table text must be empty; literal text lives in cells')
                elif b['rows']: raise ValueError('Only table blocks may contain rows')
                for r in b['rows']:
                    ids.append(r['id'])
                    occupied=set()
                    for c in r['cells']:
                        ids.append(c['id'])
                        slots=set(range(c['column'],c['column']+c['column_span']))
                        if occupied & slots: raise ValueError('Overlapping cells')
                        occupied |= slots
        if len(ids)!=len(set(ids)): raise ValueError('Duplicate reading IDs')
        if [p['page'] for p in value['pages']] != list(range(1,len(value['pages'])+1)):
            raise ValueError('Pages must be contiguous and ordered')
    if name == 'invoice' and [l['position'] for l in value['lines']] != list(range(1,len(value['lines'])+1)):
        raise ValueError('Line positions must preserve order, starting at 1')
    return value

def manifest(path, require_selection=True):
    path=Path(path).resolve()
    m=validate('manifest', load(path))
    if require_selection and m['selection_status']=='awaiting_user_selection': raise ValueError('User selection missing')
    if m['selection_status']=='selected' and len(m['documents']) != 50: raise ValueError('Exactly 50 selected documents required')
    ids=[d['file_id'] for d in m['documents']]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate filenames')
    for d in m['documents']:
        pdf=(path.parent/d['path']).resolve()
        if pdf.name!=d['file_id'] or file_hash(pdf)!=d['sha256']: raise ValueError('Source identity/hash mismatch: '+d['file_id'])
    return m

def paths(base, file_id):
    k=key(file_id)
    return {s:Path(base)/'references'/s/(k+'.json') for s in ['stage2','stage3','provenance']}

def leaves(value, prefix=''):
    if isinstance(value,dict):
        for k,v in value.items(): yield from leaves(v,prefix+'/'+k.replace('~','~0').replace('/','~1'))
    elif isinstance(value,list):
        for i,v in enumerate(value): yield from leaves(v,prefix+'/'+str(i))
    else: yield prefix,value

def pointer(value, ptr):
    for part in ptr.split('/')[1:]:
        part=part.replace('~1','/').replace('~0','~')
        value=value[int(part)] if isinstance(value,list) else value[part]
    return value

def reference(base, entry):
    pp=paths(base,entry['file_id'])
    r=validate('reading',load(pp['stage2']))
    i=validate('invoice',load(pp['stage3']))
    p=validate('provenance',load(pp['provenance']))
    for a in [r,i,p]:
        if a['file_id']!=entry['file_id']: raise ValueError('Reference file identity mismatch')
    if p['source_sha256']!=entry['sha256']: raise ValueError('Reference source hash mismatch')
    ids={}
    for page in r['pages']:
        for b in page['blocks']:
            ids[b['id']]=page['page']
            for row in b['rows']:
                ids[row['id']]=page['page']
                for c in row['cells']: ids[c['id']]=page['page']
    for ptr,links in p['pointers'].items():
        pointer(i,ptr)
        if not links: raise ValueError('Empty provenance link')
        for link in links:
            if not link['reference_ids'] or any(ids.get(x)!=link['page'] for x in link['reference_ids']): raise ValueError('Invalid provenance ID/page')
    for stage in ['stage2','stage3']:
        meta=p[stage]
        if meta['artifact_sha256']!=file_hash(pp[stage]): raise ValueError('Reference changed; update review status/hash')
        if meta['status']=='human_verified':
            if not meta['reviewer'] or not meta['reviewed_at'] or not meta['complete']: raise ValueError('Incomplete human review attestation')
        if meta['complete'] and meta['inspected_pages']!=[x['page'] for x in r['pages']]: raise ValueError('All pages must be inspected')
        for ptr in meta['excluded_pointers']:
            pointer(r if stage=='stage2' else i,ptr)
    if p['stage3']['complete']:
        for ptr,val in leaves(i):
            if val is not None and not ptr.startswith(('/schema_version','/file_id','/issues','/document_type')) and not ptr.endswith('/position'):
                if ptr not in p['pointers']: raise ValueError('Missing field provenance: '+ptr)
    return r,i,p
