from copy import deepcopy
from decimal import Decimal
import pytest
from benchmark.interpretation import blank,candidates,header_questions,assemble,FIELDS,date_value
from benchmark.scoring import score_invoice,score_reading,decimal
from benchmark.adapters import text_reading
from benchmark.core import validate

@pytest.fixture
def invoice():
    i=blank('synthetic.pdf');i.update(document_type='invoice',invoice_number='001-AB',issue_date='2026-01-23',currency='EUR')
    i['supplier'].update(name='Example S.L.',tax_id='B00123456');i['customer']['tax_id']='A12345678'
    i['lines']=[dict(position=1,description='Repeated item',quantity=None,amount='1234.50'),dict(position=2,description='Repeated item',quantity='2',amount='12.00')]
    i['taxes']=[dict(label='IVA',rate_percent='21',amount='261.77')];i['totals'].update(taxable_base='1246.50',total='1508.27')
    i['annotations']=[dict(kind='note',text='Payment in 30 days'),dict(kind='stamp',text='RECIBIDO')]
    return i

def test_european_decimal_and_exact_values(invoice):
    assert decimal('1.234,50')==Decimal('1234.50')
    assert decimal('1,234.50') is None
    p=deepcopy(invoice);p['totals']['total']='1508.2700'
    s=score_invoice(invoice,p)
    assert s['all_scored_fields_correct']
    d=next(d for d in s['details'] if d['field']=='/totals/total')
    assert d['normalized'] and not d['exact']
    p['totals']['total']='1508,27'
    assert not score_invoice(invoice,p)['schema_valid'] # schema requires canonical decimals

def test_identifiers_never_fuzzy(invoice):
    p=deepcopy(invoice);p['invoice_number']='1-AB';p['supplier']['tax_id']='B00123457'
    s=score_invoice(invoice,p)
    assert s['fields']['incorrect']==2
    assert not s['all_scored_fields_correct']

def test_duplicate_rows_alignment_order_and_amounts(invoice):
    p=deepcopy(invoice);p['lines'].reverse()
    for n,l in enumerate(p['lines'],1):l['position']=n
    s=score_invoice(invoice,p);c=s['collections']['lines']
    assert c['correct_rows']==2 and c['order_inversions']==1
    assert not s['all_scored_fields_correct']

def test_missing_quantity_not_inferred(invoice):
    p=deepcopy(invoice);p['lines'][0]['quantity']='1'
    s=score_invoice(invoice,p)
    assert any(x['field']=='/lines/0/quantity' and x['category']=='unsupported' for x in s['details'])

def test_ambiguous_date_excluded_and_abstention(invoice):
    invoice['issue_date']=None;invoice['issues']=[dict(field='/issue_date',kind='ambiguous',raw_text='03/04/2026',candidates=['2026-03-04','2026-04-03'])]
    p=deepcopy(invoice);p['issue_date']='2026-04-03'
    s=score_invoice(invoice,p)
    assert '/issue_date' in s['excluded']
    assert s['abstention']==[dict(field='/issue_date',abstained=False)]
    assert date_value('03/04/2026') is None and date_value('23/04/2026')=='2026-04-23'

def test_dropped_extra_and_duplicate_lines(invoice):
    p=deepcopy(invoice);p['lines']=p['lines'][:1]
    s=score_invoice(invoice,p);assert s['collections']['lines']['recall']==0.5
    p=deepcopy(invoice);p['lines'].append(dict(position=3,description='Extra',quantity=None,amount='7'))
    s=score_invoice(invoice,p);assert s['collections']['lines']['precision']==2/3
    assert not s['all_scored_fields_correct']

def test_missing_notes_and_stamps(invoice):
    p=deepcopy(invoice);p['annotations']=[]
    s=score_invoice(invoice,p);assert s['collections']['annotations']['recall']==0
    assert not s['all_scored_fields_correct']

@pytest.mark.parametrize('prediction,failure',[(None,None),('invalid JSON',None),({},'provider_failure')])
def test_invalid_and_provider_failures(invoice,prediction,failure):
    s=score_invoice(invoice,prediction,failure=failure)
    assert not s['schema_valid'] and not s['all_scored_fields_correct']
    assert s['fields']['present_value_recall']==0
    assert s['fields']['correct_null_rate']==0

def test_all_null_prediction_cannot_win(invoice):
    p=blank('synthetic.pdf');p['document_type']='invoice'
    s=score_invoice(invoice,p)
    assert s['schema_valid'] and s['fields']['present_value_recall']<0.1
    assert s['fields']['correct_null_rate']>0.5
    assert not s['all_scored_fields_correct']

def test_additional_fields_tax_coverage(invoice):
    invoice['additional_fields']=[dict(label='Reference',raw_value='ABC-01',normalized_value=None)]
    p=deepcopy(invoice);p['additional_fields']=[];p['taxes'][0]['amount']='260'
    s=score_invoice(invoice,p)
    assert s['collections']['additional_fields']['recall']==0
    assert s['collections']['taxes']['recall']==0

def test_reading_order_separate_and_numeric_strict():
    r=text_reading('x.pdf',['AB-01 123,45\nFooter note'])
    p=text_reading('x.pdf',['Footer note\nAB-01 123,45'])
    s=score_reading(r,p)
    assert s['strict']['content_recall']==1 and s['strict']['wer']>0
    assert s['tables'] is None and not s['per_kind']['footer']['comparable']
    p=text_reading('x.pdf',['AB-1 123,46'])
    assert score_reading(r,p)['strict']['numeric_identifier_recall']==0

def test_whitespace_strict_and_normalized():
    r=text_reading('x.pdf',['A  B\n123'])
    p=text_reading('x.pdf',['A B 123'])
    s=score_reading(r,p)
    assert s['strict']['cer']>0 and s['whitespace_normalized']['cer']==0

def test_reading_uncertainty_and_kinds():
    r=text_reading('x.pdf',['Invoice'])
    r['capabilities']['block_kinds']=True
    r['pages'][0]['blocks'] += [dict(id='stamp',kind='stamp',text='RECIBIDO',rows=[],uncertainties=[]),dict(id='f',kind='footer',text='Unreadable',rows=[],uncertainties=[dict(kind='unreadable',raw_text=None,description='blur')])]
    p=deepcopy(r);p['pages'][0]['blocks']=p['pages'][0]['blocks'][:1]
    s=score_reading(r,p)
    assert s['per_kind']['stamp']['content_recall']==0 and s['excluded_blocks']==['f']

def test_table_duplicates_and_spans():
    r=text_reading('x.pdf',['']);r['capabilities']['tables']=True
    b=r['pages'][0]['blocks'][0];b.update(kind='table',rows=[dict(id=f'r{i}',cells=[dict(id=f'c{i}',column=1,row_span=1,column_span=2,text='A')]) for i in range(2)])
    p=deepcopy(r);p['pages'][0]['blocks'][0]['rows'].pop()
    assert score_reading(r,p)['tables']['row_recall']==0.5

def test_candidates_source_only_and_none_options():
    r=text_reading('x.pdf',['Invoice: QX-001\nItem 1.234,50'])
    c=candidates(r);q=header_questions(c)
    assert all(v['text'] in '\n'.join(u['text'] for u in c['units']) for v in c['pool'].values())
    assert 'missing' in q['h0']['criteria'] and 'none_of_the_above' in q['h0']['criteria']
    assert not any(v['text']=='ground-truth-not-in-reading' for v in c['pool'].values())
    answers={f'h{i}':{'choice':'missing'} for i in range(len(FIELDS))}
    answers.update(document_type={'choice':'unknown'})
    answers.update({f'r{i}':{'choice':'other'} for i in range(len(c['units']))})
    out=assemble('x.pdf',c,answers);validate('invoice',out)
    assert out['lines']==[] and out['invoice_number'] is None
