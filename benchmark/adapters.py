"""Small HTTP adapters. Raw bodies retained; authorization never persisted."""
import base64
import json
import os
import re
import time
from pathlib import Path
import httpx
import fitz
from dotenv import dotenv_values
from .core import dump,load,value_hash,validate,schema
from .interpretation import RULES,candidates,header_questions,row_questions,assemble

ENV_NAMES=['FAL_KEY','JEV_API_KEY','TYPESAFE_API_KEY','HELMCODE_API_KEY','DEEPSEEK_API_KEY']

def credentials():
    dotenv=dotenv_values(Path(__file__).resolve().parents[1]/'.env')
    return {k:os.environ.get(k) or dotenv.get(k) for k in ENV_NAMES}

def redact(text):
    for secret in credentials().values():
        if secret:text=text.replace(secret,'[REDACTED]')
    # Imported HTTP dumps may contain keys that are not in this process environment.
    text=re.sub(r'(?i)("(?:authorization|api[_-]?key|access[_-]?token|password|secret)"\s*:\s*)"[^"\n]*"',r'\1"[REDACTED]"',text)
    text=re.sub(r'(?im)^(authorization|x-api-key):[^\n]*',r'\1: [REDACTED]',text)
    return text

def safe_write(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(redact(text))

def credential(provider):
    values=credentials()
    names={'fal':['FAL_KEY'],'jev':['TYPESAFE_API_KEY','JEV_API_KEY'],'helmcode':['HELMCODE_API_KEY'],'deepseek':['DEEPSEEK_API_KEY']}[provider]
    for name in names:
        if values[name]:return values[name]
    raise RuntimeError('Missing credential: '+' or '.join(names))

class Caller:
    def __init__(self,directory,max_attempts=2):
        self.directory=Path(directory);self.max_attempts=max_attempts;self.usage=[];self.latency=0.;self.attempts=0;self.models=[]
    def post(self,url,payload,provider):
        token=credential(provider)
        identity=value_hash(dict(url=url,payload=payload));folder=self.directory/identity
        folder.mkdir(parents=True,exist_ok=True)
        safe_write(folder/'request.json',json.dumps(dict(method='POST',url=url,headers={'Content-Type':'application/json'},body=payload),ensure_ascii=False,indent=2))
        cached=folder/'success.json'
        if cached.exists():
            result=load(cached);self.usage.append(result.get('usage',{}));self.models.append(result.get('model'));return result
        existing=list(folder.glob('attempt-*.json'));start=len(existing)
        for attempt in range(start+1,start+self.max_attempts+1):
            prefix=folder/f'attempt-{attempt:03}'
            dump(prefix.with_suffix('.json'),dict(status='pending',request_hash=identity))
            t=time.monotonic();self.attempts+=1
            try:
                response=httpx.post(url,json=payload,headers={'Authorization':('Key ' if provider=='fal' else 'Bearer ')+token},timeout=180)
                elapsed=time.monotonic()-t;self.latency+=elapsed
                safe_write(prefix.with_suffix('.response.txt'),response.text)
                dump(prefix.with_suffix('.json'),dict(status='received',http_status=response.status_code,latency_seconds=elapsed,request_hash=identity))
                if response.status_code>=400:
                    if response.status_code==429 or response.status_code>=500:
                        if attempt<start+self.max_attempts:time.sleep(min(2**(attempt-start),8));continue
                    raise RuntimeError('Provider HTTP '+str(response.status_code)+'; see credential-redacted response')
                result=response.json()
                if not isinstance(result,dict):raise ValueError('Provider response must be object')
                safe_write(cached,json.dumps(result,ensure_ascii=False,indent=2))
                self.usage.append(result.get('usage',{}));self.models.append(result.get('model'));return result
            except httpx.TransportError:
                elapsed=time.monotonic()-t;self.latency+=elapsed
                dump(prefix.with_suffix('.json'),dict(status='transport_failure_outcome_unknown',latency_seconds=elapsed,request_hash=identity))
                # A timeout may already be billed. Resume requires explicit retry-failed.
                raise RuntimeError('Transport failure; server outcome and cost unknown') from None
        raise RuntimeError('Bounded retry limit reached')
    def totals(self):
        usage={}
        # Read persisted attempts, including previous sessions and cached subcalls.
        latency=0.;attempts=0
        for p in self.directory.glob('*/attempt-*.json'):
            a=load(p);attempts+=1;latency+=a.get('latency_seconds',0)
        for x in self.usage:
            for k,v in x.items():
                if isinstance(v,(int,float)) and not isinstance(v,bool):usage[k]=usage.get(k,0)+v
        return dict(latency_seconds=latency,attempts=attempts,usage=usage,resolved_model=next((m for m in self.models if m),None),cost_usd=None)

def text_reading(fid,texts):
    return dict(schema_version='0.1',file_id=fid,capabilities=dict(block_kinds=False,tables=False,layout=False),pages=[dict(page=i,blocks=[dict(id=f'p{i}-b001',kind='other',text=t,rows=[],uncertainties=[])],non_text_elements=[]) for i,t in enumerate(texts,1)])

def fal(pdf,fid,model,caller):
    doc=fitz.open(pdf);texts=[]
    for page in doc:
        png=page.get_pixmap(dpi=200).tobytes('png')
        payload=dict(input_image_urls=['data:image/png;base64,'+base64.b64encode(png).decode()],do_format=False,multi_page=False)
        result=caller.post('https://fal.run/'+model,payload,'fal')
        outputs=result.get('outputs')
        if not isinstance(outputs,list) or len(outputs)!=1 or not isinstance(outputs[0],str):raise ValueError('Expected exactly one OCR output string per page')
        texts.append(outputs[0])
    return text_reading(fid,texts),{'pages':len(doc),'render_dpi':200,'renderer':'PyMuPDF '+fitz.VersionBind}

def deepseek(reading,model,provider,caller):
    base='https://api.helmcode.com/v1' if provider=='helmcode' else 'https://api.deepseek.com'
    # Helmcode membership MUST have been verified with the account catalogue.
    if provider=='helmcode':
        path=Path(__file__).parent/'providers/helmcode-catalogue.json'
        if not path.exists():raise RuntimeError('Run catalogue --provider helmcode with HELMCODE_API_KEY first')
        models=load(path);ids=[m.get('id') for m in models.get('data',[])]
        if model not in ids:raise RuntimeError('Requested model absent from accessible Helmcode catalogue')
        if 'deepseek' not in model.lower():raise ValueError('This benchmark adapter requires a DeepSeek model')
    payload=dict(model=model,messages=[dict(role='system',content=RULES+'\nReturn only a JSON object following this exact schema:\n'+json.dumps(schema('invoice'))),dict(role='user',content=json.dumps(reading,ensure_ascii=False))],stream=False,max_tokens=8192)
    if provider=='deepseek':payload['response_format']={'type':'json_object'}
    result=caller.post(base+'/chat/completions',payload,provider)
    choice=result['choices'][0]
    if choice.get('finish_reason') not in ['stop',None]:raise ValueError('Incomplete provider response: '+str(choice.get('finish_reason')))
    return json.loads(choice['message']['content'])

def jev(reading,model,caller):
    c=candidates(reading);answers={}
    def ask_batch(questions):
        result=caller.post('https://api.typesafe.ai/v1/systemone',dict(model=model,state=reading,questions=questions),'jev')
        response=result.get('answers',{})
        for qid,q in questions.items():
            answer=response.get(qid,{})
            if answer.get('type')!='choice' or answer.get('choice') not in q['criteria']:raise ValueError('Invalid or missing Jev Choice answer')
        return response
    def ask(questions):
        # Live contract: <=255 Choice options. Do not drop source candidates.
        bounded={}
        for qid,q in questions.items():
            if len(q['criteria'])<=255:
                bounded[qid]=q;continue
            sentinels={k:v for k,v in q['criteria'].items() if k in ['missing','none_of_the_above','ambiguous']}
            entries=[(k,v) for k,v in q['criteria'].items() if k not in sentinels]
            winners={}
            for start in range(0,len(entries),252):
                subid=qid+f'_group{start//252}'
                sub={**q,'criteria':{**sentinels,**dict(entries[start:start+252])}}
                response=ask_batch({subid:sub});selected=response[subid]['choice']
                if selected not in sentinels:winners[selected]=q['criteria'][selected]
            if len(winners)>252:raise ValueError('Candidate tournament exceeds supported final fan-in; no candidates silently dropped')
            bounded[qid]={**q,'criteria':{**sentinels,**winners}}
        items=list(bounded.items())
        for start in range(0,len(items),12):
            batch=dict(items[start:start+12]);response=ask_batch(batch)
            for qid in batch:answers[qid]=response[qid]
    ask(header_questions(c));ask(row_questions(c,answers))
    output=assemble(reading['file_id'],c,answers)
    dump(caller.directory.parent/'candidates.json',c)
    return output

def catalogue(provider,out):
    urls={'jev':'https://api.typesafe.ai/v1/models','helmcode':'https://api.helmcode.com/v1/models','deepseek':'https://api.deepseek.com/models'}
    r=httpx.get(urls[provider],headers={'Authorization':'Bearer '+credential(provider)},timeout=30)
    if r.status_code!=200:raise RuntimeError('Catalogue HTTP '+str(r.status_code))
    safe_write(out,r.text)
    return r.json()
