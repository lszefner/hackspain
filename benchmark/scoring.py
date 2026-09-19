"""Deterministic scorers. No provider calls and no LLM judging."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from scipy.optimize import linear_sum_assignment
from .core import leaves, validate

def ratio(a,b): return a/b if b else None
def ws(s): return ' '.join(unicodedata.normalize('NFC',s).split())
def decimal(s):
    """Accept canonical decimals or explicit European grouping/decimal commas only."""
    if s is None: return None
    s=str(s).strip()
    if re.fullmatch(r'-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d+',s): s=s.replace('.','').replace(',','.')
    elif not re.fullmatch(r'-?\d+(?:\.\d+)?',s): return None
    try: return Decimal(s)
    except InvalidOperation: return None

def normal(ptr,value):
    if value is None: return None
    if ptr.endswith(('/amount','/quantity','/rate_percent','/taxable_base','/total')):
        d=decimal(value)
        return d if d is not None else ('invalid_decimal',value)
    if ptr.endswith('/iban'): return ''.join(value.split())
    if ptr.endswith(('/invoice_number','/tax_id','/purchase_order_reference','/issue_date','/currency','/document_type')):
        return value  # no fuzzy identifiers, case folding or speculative dates
    return ws(value) if isinstance(value,str) else value

def excluded(ptr, exclusions): return any(ptr==p or ptr.startswith(p+'/') for p in exclusions)

def leaf_result(ptr, ref, pred, present=True):
    exact=present and ref==pred
    normalized=present and normal(ptr,ref)==normal(ptr,pred)
    category='correct' if normalized else ('unsupported' if ref is None and pred is not None else 'missing' if not present or pred is None else 'incorrect')
    return dict(field=ptr,reference=ref,prediction=pred,exact=exact,normalized=normalized,category=category)

def summarize_fields(details):
    present=[x for x in details if x['reference'] is not None]
    nulls=[x for x in details if x['reference'] is None and '/extra/' not in x['field']]
    return dict(scored=len(details),exact_correct=sum(x['exact'] for x in details),normalized_correct=sum(x['normalized'] for x in details),
        present=len(present),present_correct=sum(x['normalized'] for x in present),present_value_recall=ratio(sum(x['normalized'] for x in present),len(present)),
        nulls=len(nulls),null_correct=sum(x['normalized'] for x in nulls),correct_null_rate=ratio(sum(x['normalized'] for x in nulls),len(nulls)),
        missing=sum(x['category']=='missing' for x in details),incorrect=sum(x['category']=='incorrect' for x in details),unsupported_non_null=sum(x['category']=='unsupported' for x in details))

def align_rows(ref,pred,collection, exclusions):
    """Maximum exact field agreement, one-to-one including duplicate rows; no fuzzy cutoff."""
    if not ref or not pred: return []
    weights=[]
    for i,r in enumerate(ref):
        row=[]
        for j,p in enumerate(pred):
            row.append(sum(1 for k,v in r.items() if k!='position' and v is not None and not excluded(f'/{collection}/{i}/{k}',exclusions) and normal('/'+k,v)==normal('/'+k,p.get(k))))
        weights.append(row)
    rr,pp=linear_sum_assignment(weights,maximize=True)
    return [(int(i),int(j)) for i,j in zip(rr,pp) if weights[i][j]>0]

def score_invoice(ref,pred,exclusions=(),failure=None):
    exclusions=set(exclusions)|{x['field'] for x in ref['issues']}
    validity=True; validation_error=None
    try: validate('invoice',pred)
    except (ValueError,TypeError): validity=False; validation_error='schema_invalid'
    # Invalid partial payloads cannot earn credit by supplying a few fields.
    if not validity or failure: pred={}
    details=[]; omitted=[]; abstentions=[]
    collections=['lines','taxes','annotations','additional_fields']
    for ptr,val in leaves({k:v for k,v in ref.items() if k not in collections+['issues','schema_version','file_id']}):
        if excluded(ptr,exclusions):
            omitted.append(ptr); continue
        current=pred
        try:
            for k in ptr.split('/')[1:]: current=current[k]
            has=True
        except (KeyError,TypeError): current=None; has=False
        details.append(leaf_result(ptr,val,current,has))
    for ptr in sorted(exclusions):
        current=pred
        try:
            for k in ptr.split('/')[1:]: current=current[int(k)] if isinstance(current,list) else current[k]
        except (KeyError,IndexError,TypeError,ValueError): current=None
        abstentions.append(dict(field=ptr,abstained=current is None))
    coverage={}
    for name in collections:
        if excluded('/'+name,exclusions):
            omitted.append('/'+name); coverage[name]={'excluded':True}; continue
        rr=ref[name]; pp=pred.get(name,[])
        pairs=align_rows(rr,pp,name,exclusions)
        mapping=dict(pairs); matched_pred={j for i,j in pairs}
        exact_rows=0; normalized_rows=0
        for i,r in enumerate(rr):
            p=pp[mapping[i]] if i in mapping else {}
            row_details=[]
            for k,v in r.items():
                if k=='position': continue
                ptr=f'/{name}/{i}/{k}'
                if excluded(ptr,exclusions): omitted.append(ptr); continue
                row_details.append(leaf_result(ptr,v,p.get(k),k in p))
            details.extend(row_details)
            if row_details and i in mapping:
                exact_rows+=all(x['exact'] for x in row_details)
                normalized_rows+=all(x['normalized'] for x in row_details)
        extras=[j for j in range(len(pp)) if j not in matched_pred]
        for j in extras:
            for k,v in pp[j].items():
                if k!='position': details.append(leaf_result(f'/{name}/extra/{j}/{k}',None,v))
        order=[j for i,j in sorted(pairs)]
        inversions=sum(order[a]>order[b] for a in range(len(order)) for b in range(a+1,len(order)))
        coverage[name]=dict(reference_count=len(rr),prediction_count=len(pp),aligned=len(pairs),
            exact_rows=exact_rows,correct_rows=normalized_rows,precision=ratio(normalized_rows,len(pp)),recall=ratio(normalized_rows,len(rr)),
            missing_rows=[i for i in range(len(rr)) if i not in mapping],extra_rows=extras,alignment=[list(p) for p in pairs],
            order_inversions=inversions,order_correct=len(pairs)==len(rr)==len(pp) and inversions==0)
    # Absence in reference does not independently prove lack of document support.
    summary=summarize_fields(details)
    all_correct=bool(details) and validity and not failure and all(d['normalized'] for d in details) and all(c.get('excluded') or c['order_correct'] for c in coverage.values())
    return dict(schema_valid=validity and not failure,validation_error=validation_error,failure=failure,
        fields=summary,collections=coverage,details=details,excluded=sorted(set(omitted)|exclusions),abstention=abstentions,
        all_scored_fields_correct=all_correct,unsupported_definition='Non-null prediction where complete reference is null; document support requires provenance review.')

def edit_distance(a,b):
    if len(a)<len(b): a,b=b,a
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        row=[i]
        for j,y in enumerate(b,1): row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=row
    return previous[-1]

def text_metrics(ref,pred):
    chars=edit_distance(ref,pred); rw=ref.split(); pw=pred.split(); words=edit_distance(rw,pw)
    rc=Counter(rw); pc=Counter(pw); hit=sum((rc&pc).values())
    nums=lambda s: Counter(t for t in re.findall(r'[\w]+(?:[.,/\-][\w]+)*',s) if any(c.isdigit() for c in t))
    rn=nums(ref); pn=nums(pred); nh=sum((rn&pn).values())
    return dict(char_edits=chars,reference_chars=len(ref),cer=ratio(chars,len(ref)),word_edits=words,reference_words=len(rw),wer=ratio(words,len(rw)),
        content_token_hits=hit,content_recall=ratio(hit,len(rw)),content_precision=ratio(hit,len(pw)),prediction_words=len(pw),
        missing_tokens=dict(rc-pc),extra_tokens=dict(pc-rc),numeric_identifier_hits=nh,numeric_identifier_reference=sum(rn.values()),
        numeric_identifier_prediction=sum(pn.values()),numeric_identifier_recall=ratio(nh,sum(rn.values())),numeric_identifier_precision=ratio(nh,sum(pn.values())),
        missing_numeric_identifiers=dict(rn-pn),extra_numeric_identifiers=dict(pn-rn))

def block_text(b):
    return '\n'.join('\t'.join(c['text'] for c in r['cells']) for r in b['rows']) if b['kind']=='table' else b['text']

def reading_text(r): return '\n\f\n'.join('\n'.join(block_text(b) for b in p['blocks']) for p in r['pages'])

def score_reading(ref,pred,exclusions=(),failure=None):
    valid=True
    try: validate('reading',pred)
    except (ValueError,TypeError): valid=False
    if not valid or failure: pred={'pages':[],'capabilities':dict(tables=False,block_kinds=False,layout=False)}
    # Uncertain blocks are excluded as a whole; uncertain text cannot act as factual truth.
    rr={'pages':[]}; omitted=[]
    for pi,p in enumerate(ref['pages']):
        blocks=[]
        for bi,b in enumerate(p['blocks']):
            ptr=f'/pages/{pi}/blocks/{bi}'
            if b['uncertainties'] or excluded(ptr,exclusions) or any(x.startswith(ptr+'/') for x in exclusions): omitted.append(b['id'])
            else: blocks.append(b)
        rr['pages'].append(dict(page=p['page'],blocks=blocks))
    rt=reading_text(rr); pt=reading_text(pred)
    strict=text_metrics(rt,pt); normalized=text_metrics(ws(rt),ws(pt))
    per_kind={}
    for kind in ['body','note','stamp','footer']:
        choose=lambda b: b['kind']==kind if kind!='body' else b['kind'] not in ['note','stamp','footer']
        a='\n'.join(block_text(b) for p in rr['pages'] for b in p['blocks'] if choose(b))
        if pred['capabilities']['block_kinds']:
            b='\n'.join(block_text(b) for p in pred['pages'] for b in p['blocks'] if choose(b))
            per_kind[kind]={'comparable':True,**text_metrics(ws(a),ws(b))}
        else:
            # Global token matches for a subset can misattribute repeated words, so do not claim completeness.
            per_kind[kind]={'comparable':False,'metrics':None,'reference_words':len(a.split()),'reason':'Provider has no comparable block kinds'}
    table=None
    if ref['capabilities']['tables'] and pred['capabilities']['tables']:
        def rows(r):
            return [tuple((ws(c['text']),c['column'],c['row_span'],c['column_span']) for c in row['cells']) for p in r['pages'] for b in p['blocks'] for row in b['rows']]
        a,b=rows(rr),rows(pred); ar,br=Counter(a),Counter(b)
        ac,bc=Counter(c for row in a for c in row),Counter(c for row in b for c in row)
        table=dict(reference_rows=len(a),prediction_rows=len(b),correct_rows=sum((ar&br).values()),row_recall=ratio(sum((ar&br).values()),len(a)),row_precision=ratio(sum((ar&br).values()),len(b)),
            reference_cells=sum(ac.values()),prediction_cells=sum(bc.values()),correct_cells=sum((ac&bc).values()),cell_recall=ratio(sum((ac&bc).values()),sum(ac.values())),cell_precision=ratio(sum((ac&bc).values()),sum(bc.values())))
    return dict(schema_valid=valid and not failure,failure=failure,strict=strict,whitespace_normalized=normalized,per_kind=per_kind,tables=table,
        layout=None,excluded_blocks=omitted,ordering={'exact_text_order':rt==pt,'whitespace_normalized_order':ws(rt)==ws(pt)},
        limitations='Token multisets measure content independent of order, not semantic completeness. No coordinate metric. Table matching ignores table assignment/order. Excluded blocks can leave unalignable prediction text counted as extra; inspect exclusions.')
