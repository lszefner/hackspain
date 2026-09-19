import html
from pathlib import Path
from urllib.parse import quote
from .core import *

def render(mp):
    import fitz
    mp=Path(mp).resolve();m=manifest(mp)
    for e in m['documents']:
        doc=fitz.open(mp.parent/e['path']);dest=mp.parent/'review/pages'/key(e['file_id']);dest.mkdir(parents=True,exist_ok=True)
        for page in doc:page.get_pixmap(dpi=150).save(dest/f'{page.number+1}.png')

def packet(mp):
    mp=Path(mp).resolve();m=manifest(mp);base=mp.parent;rows=[];review=[];unresolved=[]
    h=html.escape
    for e in m['documents']:
        pp=paths(base,e['file_id']);p=load(pp['provenance']) if pp['provenance'].exists() else None
        entry=dict(file_id=e['file_id'],sha256=e['sha256'],split=e['split'],pdf='../'+e['path'],
            reading='../references/stage2/'+key(e['file_id'])+'.json',invoice='../references/stage3/'+key(e['file_id'])+'.json',provenance='../references/provenance/'+key(e['file_id'])+'.json',
            stage2_status=p['stage2']['status'] if p else 'missing',stage3_status=p['stage3']['status'] if p else 'missing',unresolved=p['unresolved'] if p else ['References missing'])
        review.append(entry)
        for issue in entry['unresolved']:unresolved.append(dict(file_id=e['file_id'],issue=issue))
        links=' · '.join(f'<a href="{quote(entry[k],safe="/.")}">{label}</a>' for k,label in [('pdf','Original PDF'),('reading','Reading JSON'),('invoice','Invoice JSON'),('provenance','Provenance/review')])
        page_links=' '.join(f'<a href="pages/{key(e["file_id"])}/{n}.png">Page {n}</a>' for n in (p['stage2']['inspected_pages'] if p else []))
        issues='<ul>'+''.join('<li>'+h(x)+'</li>' for x in entry['unresolved'])+'</ul>' if entry['unresolved'] else 'No unresolved issues recorded; still requires human review.'
        rows.append(f'<tr><td>{h(e["file_id"])}<br>{h(e["split"])}</td><td>{links}<br>{page_links}</td><td>{entry["stage2_status"]} / {entry["stage3_status"]}</td><td>{issues}</td></tr>')
    dump(base/'review/manifest.json',dict(schema_version='0.1',generated_at=now(),documents=review))
    dump(base/'review/unresolved.json',unresolved)
    content='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Invoice reference review</title>
<style>body{font:16px system-ui;margin:32px;line-height:1.5}table{border-collapse:collapse;width:100%}th,td{text-align:left;border:1px solid #bbb;padding:12px;vertical-align:top}a{color:#0645ad}td:first-child{overflow-wrap:anywhere}</style>
<h1>Invoice reference review</h1><p>All Codex annotations start as drafts. Read every page of the original PDF, then check the literal reading, invoice fields, provenance and exclusions. Unresolved readings are not factual answers. Stage 2 and stage 3 are reviewed separately. Do not use model predictions to silently correct references.</p>
<p>Use the README review commands after a human finishes inspection. Held-out answers are for review and final evaluation, never prompt tuning.</p><table><thead><tr><th>Document / split</th><th>Artifacts</th><th>Reading / invoice status</th><th>Unresolved / review notes</th></tr></thead><tbody>'''+''.join(rows)+'</tbody></table></html>'
    (base/'review/index.html').write_text(content)
    return len(review),len(unresolved)

def attest(mp,file_id,stage,reviewer,attestation):
    if attestation!='I personally inspected every page and verified this reference':raise ValueError('Exact human review attestation required; Codex must not invoke this for itself')
    m=manifest(mp);e=next(d for d in m['documents'] if d['file_id']==file_id);base=Path(mp).resolve().parent;pp=paths(base,file_id)
    p=load(pp['provenance']);p[stage].update(status='human_verified',reviewer=reviewer,reviewed_at=now(),artifact_sha256=file_hash(pp[stage]))
    # Validate candidate attestation before writing.
    old=load(pp['provenance']);dump(pp['provenance'],p)
    try:reference(base,e)
    except Exception:dump(pp['provenance'],old);raise
    packet(mp)
