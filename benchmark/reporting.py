from collections import Counter,defaultdict
from decimal import Decimal
from pathlib import Path
import re
from .core import *
from .runner import get_run,TRACKS
from .scoring import score_invoice,score_reading,ratio,summarize_fields
from .interpretation import candidate_metrics

def average(values):
    values=[v for v in values if v is not None]
    return sum(values)/len(values) if values else None

def aggregate(docs,track):
    scores=[d['score'] for d in docs if d.get('score') is not None]
    out=dict(documents=len(docs),scorable_references=len(scores),reference_coverage=ratio(len(scores),len(docs)),statuses=dict(Counter(d['status'] for d in docs)),
        schema_valid_documents=sum(s['schema_valid'] for s in scores),schema_valid_rate=ratio(sum(s['schema_valid'] for s in scores),len(docs)))
    if track!='ocr':
        details=[x for s in scores for x in s['details']];out['fields']=summarize_fields(details)
        out['all_scored_fields_correct_documents']=sum(s['all_scored_fields_correct'] for s in scores)
        out['all_scored_fields_correct_rate']=ratio(out['all_scored_fields_correct_documents'],len(docs))
        out['macro_present_value_recall']=average([s['fields']['present_value_recall'] for s in scores])
        out['macro_correct_null_rate']=average([s['fields']['correct_null_rate'] for s in scores])
        out['excluded_fields']=sum(len(s['excluded']) for s in scores)
        out['per_field']={}
        grouped=defaultdict(list)
        for d in details:grouped[re.sub(r'/\d+(?=/|$)','/*',d['field'])].append(d)
        for field,items in grouped.items():out['per_field'][field]=summarize_fields(items)
        out['collections']={}
        for c in ['lines','taxes','annotations','additional_fields']:
            cc=[s['collections'][c] for s in scores if not s['collections'][c].get('excluded')]
            totals={k:sum(x[k] for x in cc) for k in ['reference_count','prediction_count','aligned','exact_rows','correct_rows','order_inversions']}
            totals.update(precision=ratio(totals['correct_rows'],totals['prediction_count']),recall=ratio(totals['correct_rows'],totals['reference_count']))
            out['collections'][c]=totals
        jc=[d['candidate_metrics'] for d in docs if d.get('candidate_metrics')]
        out['jev_candidate_metric_coverage']=ratio(len(jc),len(docs))
        out['jev_macro_candidate_coverage']=average([c['candidate_coverage'] for c in jc])
        out['jev_macro_assignment_accuracy_when_covered']=average([c['assignment_accuracy_when_covered'] for c in jc])
    else:
        for mode in ['strict','whitespace_normalized']:
            ss=[s[mode] for s in scores]
            total={k:sum(x[k] for x in ss) for k in ['char_edits','reference_chars','word_edits','reference_words','prediction_words','content_token_hits','numeric_identifier_hits','numeric_identifier_reference','numeric_identifier_prediction']}
            total.update(cer=ratio(total['char_edits'],total['reference_chars']),wer=ratio(total['word_edits'],total['reference_words']),content_recall=ratio(total['content_token_hits'],total['reference_words']),content_precision=ratio(total['content_token_hits'],total['prediction_words']),numeric_identifier_recall=ratio(total['numeric_identifier_hits'],total['numeric_identifier_reference']),numeric_identifier_precision=ratio(total['numeric_identifier_hits'],total['numeric_identifier_prediction']))
            out[mode]=dict(totals=total,macro={k:average([x[k] for x in ss]) for k in ['cer','wer','content_recall','content_precision','numeric_identifier_recall','numeric_identifier_precision']})
        out['table_metric_coverage']=ratio(sum(s['tables'] is not None for s in scores),len(docs))
        out['block_kind_metric_coverage']=ratio(sum(s['per_kind']['body']['comparable'] for s in scores),len(docs))
        out['excluded_blocks']=sum(len(s['excluded_blocks']) for s in scores)
        out['per_kind']={}
        for k in ['body','note','stamp','footer']:
            rows=[s['per_kind'][k] for s in scores if s['per_kind'][k]['comparable']]
            out['per_kind'][k]=dict(comparable_documents=len(rows),reference_words=sum(x['reference_words'] for x in rows),word_edits=sum(x['word_edits'] for x in rows),macro_content_recall=average([x['content_recall'] for x in rows]))
        tables=[s['tables'] for s in scores if s['tables'] is not None]
        out['table_totals']={k:sum(x[k] for x in tables) for k in ['reference_rows','prediction_rows','correct_rows','reference_cells','prediction_cells','correct_cells']}
    costs=[Decimal(d['operations']['cost_usd']) for d in docs if d.get('operations',{}).get('cost_usd') is not None]
    latencies=[d['operations']['latency_seconds'] for d in docs if d.get('operations',{}).get('latency_seconds') is not None]
    usage=defaultdict(float)
    for d in docs:
        for k,v in d.get('operations',{}).get('usage',{}).items():
            if isinstance(v,(int,float)):usage[k]+=v
    out['operations']=dict(known_cost_usd=str(sum(costs)) if costs else None,cost_known_documents=len(costs),cost_unknown_documents=len(docs)-len(costs),total_cost_usd=str(sum(costs)) if len(costs)==len(docs) and docs else None,
        known_latency_seconds=sum(latencies) if latencies else None,latency_known_documents=len(latencies),usage_totals=dict(usage),attempts=sum(d.get('operations',{}).get('attempts',0) for d in docs))
    return out

def score_run(mp,run_id,output):
    m,r,rd=get_run(mp,run_id);base=Path(mp).resolve().parent;docs=[]
    for e in m['documents']:
        if e['file_id'] not in r['documents']:continue
        folder=rd/key(e['file_id']);record=None;prediction=None;status='missing';reference_error=None
        try:
            record=validate('record',load(folder/'record.json'));status=record['status']
            if record['source_sha256']!=e['sha256']:raise ValueError('Output source hash mismatch')
            if status=='success':
                if record['artifact_sha256']!=file_hash(folder/'output.json'):raise ValueError('Stored output hash mismatch')
                prediction=load(folder/'output.json');validate(TRACKS[r['track']],prediction)
                if prediction['file_id']!=e['file_id']:raise ValueError('Output file identity mismatch')
                if r['track']!='ocr' and record['reading_sha256']!=file_hash(folder/'input-reading.json'):raise ValueError('Input reading hash mismatch')
        except FileNotFoundError:status='missing'
        except (ValueError,TypeError):status='invalid'
        sc=None;official=False;candidate=None;refhashes={}
        try:
            reading,invoice,prov=reference(base,e)
            stage='stage2' if r['track']=='ocr' else 'stage3'
            if not prov[stage]['complete']:raise ValueError('Reference is incomplete; absent annotations are not evidence of absence')
            if r['track']=='isolated' and not prov['stage2']['complete']:raise ValueError('Reference reading incomplete')
            official=prov[stage]['status']=='human_verified' and (r['track']!='isolated' or prov['stage2']['status']=='human_verified')
            # Isolated scores are tied to the exact reading supplied, not a later corrected reading.
            if r['track']=='isolated' and record and record['reading_sha256'] and record['reading_sha256']!=file_hash(paths(base,e['file_id'])['stage2']):
                if status=='success':status='invalid'
                official=False
                reference_error='Current canonical reading differs from the input used for this run'
            failure=None if status=='success' else status
            sc=score_reading(reading,prediction,prov[stage]['excluded_pointers'],failure) if r['track']=='ocr' else score_invoice(invoice,prediction,prov[stage]['excluded_pointers'],failure)
            if r['provider']=='jev' and (folder/'candidates.json').exists():candidate=candidate_metrics(invoice,sc,load(folder/'candidates.json'))
            refhashes={s:prov[s]['artifact_sha256'] for s in ['stage2','stage3']}
        except (FileNotFoundError,ValueError,TypeError,KeyError) as ex:reference_error=type(ex).__name__+': '+str(ex)
        docs.append(dict(file_id=e['file_id'],split=e['split'],status=status,reference_class='reviewed' if official else 'draft_or_unavailable',reference_error=reference_error,
            reference_hashes=refhashes,score=sc,candidate_metrics=candidate,operations={k:record[k] for k in ['latency_seconds','usage','cost_usd','attempts','resolved_model','imported']} if record else {}))
    reviewed=[d for d in docs if d['reference_class']=='reviewed'];draft=[d for d in docs if d['reference_class']!='reviewed']
    report=dict(schema_version='0.1',run=r,reused_http_cache=load(rd/'reused-http-cache.json') if (rd/'reused-http-cache.json').exists() else None,generated_at=now(),selected_documents=len(docs),reviewed_subset=[d['file_id'] for d in reviewed],reviewed_coverage=ratio(len(reviewed),len(docs)),
        official=aggregate(reviewed,r['track']),provisional=aggregate(draft,r['track']),all_documents_operational=aggregate(docs,r['track']),
        by_split={split:{label:aggregate([d for d in group if d['split']==split],r['track']) for label,group in [('official',reviewed),('provisional',draft)]} for split in ['development','held_out']},documents=docs,
        limitations=['Codex references are drafts, never human-verified ground truth.','Missing/failed/invalid outputs remain in document denominators. Unavailable references are reported, never treated as absent content.','No extrapolation to all 500 invoices. Selection is positional, not random.','Candidate coverage is an optimistic value-level upper bound; grouping/role errors still count.','No LLM judge. Unsupported values mean contradicted by complete reference null, not independently proven hallucinations.','Ordering and layout are not equivalent to content coverage. See README for alignment limits.'])
    dump(output,report);write_markdown(report,Path(output).with_suffix('.md'));return report

def fmt(value):
    if value is None:return 'unavailable'
    if isinstance(value,float):return f'{value:.4f}'
    return str(value)

def write_markdown(report,path):
    r=report['run'];lines=[f'# Invoice benchmark: {r["run_id"]}',f'\nTrack: **{r["track"]}**. Provider/model: `{r["provider"]}` / `{r["model"]}`.',
        f'\nSelected: {report["selected_documents"]}. Human-reviewed coverage: {len(report["reviewed_subset"])}/{report["selected_documents"]}.',
        '\nDraft-reference results are **PROVISIONAL**. No reviewed invoices means no official accuracy result.',
        '\n| Reference class | Documents | Scorable | Schema valid | Main metric | Whole-document correct |',
        '|---|---:|---:|---:|---:|---:|']
    for label in ['official','provisional']:
        a=report[label];main=a.get('macro_present_value_recall') if r['track']!='ocr' else a['whitespace_normalized']['macro']['content_recall']
        lines.append(f'| {label} | {a["documents"]} | {a["scorable_references"]} | {fmt(a["schema_valid_rate"])} | {fmt(main)} | {fmt(a.get("all_scored_fields_correct_rate"))} |')
    lines+=['\nMain metric: macro present-value recall (interpretation), or macro token content recall (OCR). Correct-null rates are reported separately in JSON.',
        '\n## Operations\n','```json',json.dumps(report['all_documents_operational']['operations'],indent=2),'```',
        '\n## Per-document results\n','| File | Split | Reference | Output | Main metric | Exclusions |','|---|---|---|---|---:|---:|']
    for d in report['documents']:
        s=d['score'];v=None if s is None else s['fields']['present_value_recall'] if r['track']!='ocr' else s['whitespace_normalized']['content_recall']
        ex=None if s is None else len(s.get('excluded',s.get('excluded_blocks',[])))
        lines.append(f'| {d["file_id"]} | {d["split"]} | {d["reference_class"]} | {d["status"]} | {fmt(v)} | {fmt(ex)} |')
    lines+=['\n## Limitations\n']+['- '+s for s in report['limitations']]
    lines+=['\nFull per-field errors, row alignment, exclusions, strict/normalized metrics, split aggregates and reviewed filenames are in the adjacent JSON report.']
    Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_text('\n'.join(lines)+'\n')
