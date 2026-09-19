from pathlib import Path
import json
import pytest
from benchmark.core import dump,load,file_hash,key,paths,validate
from benchmark.runner import create_run,execute,reading_source,import_output
from benchmark.reporting import score_run
from benchmark.adapters import Caller,text_reading
from benchmark.interpretation import blank

@pytest.fixture
def workspace(tmp_path):
    fid='fixture.pdf';pdf=tmp_path/fid;pdf.write_bytes(b'synthetic fixture; no PDF rendering needed')
    e=dict(file_id=fid,path=fid,sha256=file_hash(pdf),split='development')
    mp=tmp_path/'manifest.json';dump(mp,dict(schema_version='0.1',selection_status='synthetic_fixture',selection_source='test only',documents=[e]))
    pp=paths(tmp_path,fid);reading=text_reading(fid,['Invoice 01']);invoice=blank(fid);invoice['invoice_number']='01';invoice['document_type']='invoice'
    dump(pp['stage2'],reading);dump(pp['stage3'],invoice)
    meta=lambda s:dict(author='Test fixture',source='synthetic_fixture',status='draft',reviewer=None,reviewed_at=None,artifact_sha256=file_hash(pp[s]),complete=True,inspected_pages=[1],excluded_pointers=[])
    dump(pp['provenance'],dict(schema_version='0.1',file_id=fid,source_sha256=e['sha256'],stage2=meta('stage2'),stage3=meta('stage3'),pointers={'/invoice_number':[dict(page=1,reference_ids=['p1-b001'])]},unresolved=[]))
    return mp,e

def test_missing_outputs_stay_in_denominator_and_official_separate(workspace):
    mp,e=workspace;create_run(mp,'r','isolated','import','test',provisional=True)
    report=score_run(mp,'r',mp.parent/'report.json')
    assert report['selected_documents']==1 and report['reviewed_coverage']==0
    assert report['official']['documents']==0
    assert report['provisional']['all_scored_fields_correct_rate']==0
    assert report['provisional']['operations']['total_cost_usd'] is None

def test_reviewed_subset_only(workspace):
    mp,e=workspace;pp=paths(mp.parent,e['file_id']);p=load(pp['provenance'])
    for s in ['stage2','stage3']:p[s].update(status='human_verified',reviewer='Synthetic fixture reviewer, not real invoices',reviewed_at='2026-09-19')
    dump(pp['provenance'],p);create_run(mp,'r','isolated','import','test')
    report=score_run(mp,'r',mp.parent/'report.json')
    assert report['official']['documents']==1 and report['provisional']['documents']==0
    assert report['official']['all_scored_fields_correct_rate']==0

def test_isolated_runner_does_not_read_invoice_answers(workspace):
    mp,e=workspace;pp=paths(mp.parent,e['file_id']);pp['stage3'].unlink()
    run=create_run(mp,'r','isolated','jev','jev-latest',provisional=True)
    assert reading_source(mp.parent,run,e)['pages'][0]['blocks'][0]['text']=='Invoice 01'

def test_review_required_and_reference_mutation(workspace):
    mp,e=workspace;run=create_run(mp,'r','isolated','jev','jev-latest')
    with pytest.raises(ValueError,match='unreviewed'):reading_source(mp.parent,run,e)
    pp=paths(mp.parent,e['file_id']);dump(pp['stage2'],text_reading(e['file_id'],['changed']))
    with pytest.raises(ValueError,match='hash'):reading_source(mp.parent,run,e)

def test_config_change_cannot_reuse_cache(workspace):
    mp,e=workspace;create_run(mp,'r','ocr','fal','fal-ai/got-ocr/v2')
    with pytest.raises(ValueError,match='configuration'):create_run(mp,'r','ocr','fal','changed')

def test_import_invalid_json_and_failure_record(workspace):
    mp,e=workspace;create_run(mp,'r','ocr','import','test')
    raw=mp.parent/'raw.txt';raw.write_text('{not json')
    meta=mp.parent/'meta.json';dump(meta,dict(source_sha256=e['sha256'],latency_seconds=None,usage={},cost_usd=None,attempts=1,resolved_model=None))
    import_output(mp,'r',e['file_id'],raw,raw,raw,meta)
    report=score_run(mp,'r',mp.parent/'report.json')
    assert report['provisional']['statuses']=={'invalid':1}
    assert report['provisional']['whitespace_normalized']['totals']['content_recall']==0

def test_resume_does_not_repeat_completed_provider_calls(tmp_path,monkeypatch):
    import benchmark.adapters as a
    monkeypatch.setattr(a,'credential',lambda p:'secret');monkeypatch.setattr(a,'credentials',lambda:{'key':'secret'})
    calls=[]
    class Response:
        status_code=200;text='{"model":"test","usage":{"input_tokens":3},"result":"yes"}'
        def json(self):return json.loads(self.text)
    monkeypatch.setattr(a.httpx,'post',lambda *args,**kwargs:(calls.append(1) or Response()))
    c=Caller(tmp_path);assert c.post('https://test.invalid',{'state':'A'},'jev')['result']=='yes'
    assert Caller(tmp_path).post('https://test.invalid',{'state':'A'},'jev')['result']=='yes'
    assert len(calls)==1
    assert all('secret' not in p.read_text() for p in tmp_path.rglob('*') if p.is_file())

def test_bounded_retry_and_raw_failures(tmp_path,monkeypatch):
    import benchmark.adapters as a
    monkeypatch.setattr(a,'credential',lambda p:'secret');monkeypatch.setattr(a.time,'sleep',lambda n:None)
    calls=[]
    class Response:status_code=503;text='service unavailable'
    monkeypatch.setattr(a.httpx,'post',lambda *args,**kwargs:(calls.append(1) or Response()))
    c=Caller(tmp_path,max_attempts=2)
    with pytest.raises(RuntimeError,match='HTTP 503'):c.post('https://test.invalid',{},'jev')
    assert len(calls)==2 and len(list(tmp_path.rglob('*.response.txt')))==2

def test_failure_execution_and_resume(workspace,monkeypatch):
    import benchmark.adapters as a
    mp,e=workspace;create_run(mp,'r','ocr','fal','fal-ai/got-ocr/v2')
    calls=[]
    def fail(*args):calls.append(1);raise RuntimeError('provider unavailable')
    monkeypatch.setattr(a,'fal',fail);execute(mp,'r');execute(mp,'r')
    assert len(calls)==1
    execute(mp,'r',retry_failed=True);assert len(calls)==2
    assert score_run(mp,'r',mp.parent/'report.json')['provisional']['statuses']=={'failed':1}

def test_choice_limit_preserves_every_candidate(monkeypatch,tmp_path):
    import benchmark.adapters as a
    from benchmark.interpretation import candidates
    reading=text_reading('x.pdf',[' '.join('word'+str(i) for i in range(70))])
    calls=[]
    class FakeCaller:
        directory=tmp_path/'raw'
        def post(self,url,payload,provider):
            calls.append(payload)
            return dict(answers={k:dict(type='choice',choice='unknown' if k=='document_type' else 'other' if k.startswith('r') else 'missing') for k in payload['questions']})
    a.jev(reading,'fixture',FakeCaller())
    questions=[q for call in calls for q in call['questions'].values()]
    assert all(len(q['criteria'])<=255 for q in questions)
    offered=set(cid for q in questions for cid in q['criteria'] if cid.startswith('c'))
    assert set(candidates(reading)['pool'])<=offered
    assert all(call['state']==reading for call in calls)

def test_redacts_imported_unknown_credentials():
    from benchmark.adapters import redact
    text='{"headers":{"Authorization":"Bearer different-secret"},"api_key":"unknown-key","text":"invoice 42"}'
    safe=redact(text)
    assert 'different-secret' not in safe and 'unknown-key' not in safe and 'invoice 42' in safe

def test_end_to_end_uses_only_saved_actual_ocr(workspace):
    mp,e=workspace;ocr=create_run(mp,'ocr','ocr','import','test')
    d=mp.parent/'runs/ocr'/key(e['file_id'])
    reading=text_reading(e['file_id'],['ACTUAL OCR, not reference']);dump(d/'output.json',reading)
    record=load(d/'record.json');record.update(status='success',artifact_sha256=file_hash(d/'output.json'));dump(d/'record.json',record)
    r=create_run(mp,'end','end_to_end','jev','test',reading_run='ocr')
    assert reading_source(mp.parent,r,e)==reading
    record['status']='failed';dump(d/'record.json',record)
    with pytest.raises(ValueError,match='unavailable'):reading_source(mp.parent,r,e)
