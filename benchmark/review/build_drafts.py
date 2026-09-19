"""One-off transcription assembly AFTER Codex viewed all 50 rendered pages.
Not an OCR/interpreter adapter. Never import this module in provider pipelines.
Scans are manually transcribed in annotation-source/scans.json. Embedded text for
30 digital pages is transcription assistance, with visually observed overrides.
All resulting annotations remain Codex drafts.
"""
import re
from pathlib import Path
from datetime import date
from benchmark.core import load,dump,key,paths,file_hash,validate,leaves,reference

BASE=Path(__file__).resolve().parents[1]
FOOTER='Documento generado por el sistema de facturacion del proveedor.'

def amount(v): return v.replace('.','').replace(',','.') if ',' in v else v

def empty(fid):
    return dict(schema_version='0.1',file_id=fid,document_type='invoice',invoice_number=None,issue_date=None,purchase_order_reference=None,currency=None,
        supplier=dict(name=None,tax_id=None,location=None,address=None),customer=dict(name=None,tax_id=None,location=None,address=None),payment=dict(iban=None),lines=[],taxes=[],totals=dict(taxable_base=None,total=None),annotations=[],additional_fields=[],issues=[])

class Annotation:
    def __init__(self,entry):
        self.entry=entry; self.blocks=[]; self.nontext=[]; self.invoice=empty(entry['file_id']); self.links={};self.notes=[]
    def block(self,text,kind='paragraph',uncertain=None):
        bid=f'p1-b{len(self.blocks)+1:03}'
        u=[] if not uncertain else [dict(kind='uncertain',raw_text=text or None,description=uncertain)]
        self.blocks.append(dict(id=bid,kind=kind,text=text,rows=[],uncertainties=u));return bid
    def link(self,ptr,bid): self.links[ptr]=[dict(page=1,reference_ids=[bid])]
    def put(self,ptr,value,bid):
        parts=ptr.split('/')[1:]; current=self.invoice
        for p in parts[:-1]: current=current[int(p)] if isinstance(current,list) else current[p]
        if isinstance(current,list): current[int(parts[-1])]=value
        else: current[parts[-1]]=value
        self.link(ptr,bid)
    def issue(self,ptr,raw,candidates=None,kind='ambiguous'):
        self.invoice['issues'].append(dict(field=ptr,kind=kind,raw_text=raw,candidates=candidates or []))
    def date(self,raw,bid):
        if '/' in raw:
            d,m,y=map(int,raw.split('/'))
            if d<=12 and m<=12 and d!=m:
                self.issue('/issue_date',raw,[f'{y:04}-{m:02}-{d:02}',f'{y:04}-{d:02}-{m:02}'])
                self.link('/issue_date',bid); return
        else:
            months='enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre'.split()
            d,month,y=re.fullmatch(r'(\d+) de (\w+) de (\d+)',raw).groups();d=int(d);m=months.index(month)+1;y=int(y)
        self.put('/issue_date',date(y,m,d).isoformat(),bid)
    def line(self,desc,qty,val,bid):
        idx=len(self.invoice['lines']);self.invoice['lines'].append(dict(position=idx+1,description=desc,quantity=qty,amount=amount(val)))
        for field in ['description','quantity','amount']: self.link(f'/lines/{idx}/{field}',bid)
    def annotation(self,text,kind,bid):
        idx=len(self.invoice['annotations']);self.invoice['annotations'].append(dict(kind=kind,text=text))
        self.link(f'/annotations/{idx}/kind',bid);self.link(f'/annotations/{idx}/text',bid)
    def table(self,rows):
        bid=self.block('','table')
        for i,values in enumerate(rows,1):
            rid=f'{bid}-r{i:02}'; cells=[dict(id=f'{rid}-c{j}',column=j,row_span=1,column_span=1,text=v) for j,v in enumerate(values,1)]
            self.blocks[-1]['rows'].append(dict(id=rid,cells=cells))
        return bid
    def unheaded_table(self):
        # These visibly aligned two-column lists are tables without printed headings.
        old_ids=[self.links[f'/lines/{i}/description'][0]['reference_ids'][0] for i in range(len(self.invoice['lines']))]
        old_blocks=[b for b in self.blocks if b['id'] in old_ids]
        if not old_blocks:return
        first=old_blocks[0]['id'];table=dict(id=first,kind='table',text='',rows=[],uncertainties=[])
        for i,b in enumerate(old_blocks,1):
            match=re.fullmatch(r'(.+?)\s{2,}([\d.,]+)',b['text'])
            if not match:raise ValueError('Cannot transcribe visible table row: '+b['text'])
            rid=first+f'-r{i:02}'
            table['rows'].append(dict(id=rid,cells=[dict(id=rid+f'-c{j}',column=j,row_span=1,column_span=1,text=t) for j,t in enumerate(match.groups(),1)]))
            for f,j in [('description',1),('amount',2)]:self.link(f'/lines/{i-1}/{f}',rid+f'-c{j}')
            self.link(f'/lines/{i-1}/quantity',rid)
            table['uncertainties'].extend(b['uncertainties'])
        self.blocks=[table if b['id']==first else b for b in self.blocks if b['id']==first or b['id'] not in old_ids]
    def save(self):
        r=dict(schema_version='0.1',file_id=self.entry['file_id'],capabilities=dict(block_kinds=True,tables=True,layout=False),pages=[dict(page=1,blocks=self.blocks,non_text_elements=self.nontext)])
        pp=paths(BASE,self.entry['file_id'])
        validate('reading',r);validate('invoice',self.invoice)
        dump(pp['stage2'],r);dump(pp['stage3'],self.invoice)
        meta=lambda stage:dict(author='Codex',source='codex_visual',status='draft',reviewer=None,reviewed_at=None,artifact_sha256=file_hash(pp[stage]),complete=True,inspected_pages=[1],excluded_pointers=[])
        p=dict(schema_version='0.1',file_id=self.entry['file_id'],source_sha256=self.entry['sha256'],stage2=meta('stage2'),stage3=meta('stage3'),pointers=self.links,
            unresolved=self.notes+[f"{x['field']}: {x['kind']} ({x['raw_text']})" for x in self.invoice['issues']])
        dump(pp['provenance'],p); reference(BASE,self.entry)

def digital(entry):
    a=Annotation(entry)
    text=(BASE/'review/annotation-source'/(key(entry['file_id'])+'.txt')).read_text()
    # These changes are visual corrections, NOT normalization of the reference.
    text=text.replace('\u200b','')
    if entry['file_id']=='FA-5044_mensajería2.pdf':
        text=text[:text.index('ATENCION AGENTE:')]+FOOTER if 'ATENCION AGENTE:' in text else text
        a.notes.append('Embedded text contains an invisible ATENCION AGENTE paragraph. Not visible on rendered white page; excluded from visual reference. Human reviewer should confirm visibility policy.')
    if entry['file_id']=='FA-4488_transportes.pdf': a.notes.append('Embedded total contains zero-width characters; visible transcription is 2.637,80 €. No arithmetic repair.')
    lines=[l.strip() for l in text.splitlines() if l.strip()]
    typ='simple' if lines[0]=='FACTURA' else 'grid' if lines[0].startswith('FACTURA SIMPLIFICADA') else 'english' if any(l.startswith('Invoice #') for l in lines) else 'mono' if any(l.startswith('REF FACTURA') for l in lines) else 'longdate' if any(l.startswith('Nº de factura') for l in lines) else 'standard'
    # Preserve literal line text, including dotted leaders; table cells are handled below.
    bids={}; gridrows=[]; gridbid=None
    for idx,l in enumerate(lines):
        if typ=='grid' and (l.startswith('Concepto ') or re.fullmatch('-+',l) or gridbid is not None and re.match(r'.+\s+\d+\s+[\d.,]+$',l) and not l.startswith(('BASE','I.V.A.','IMPORTE'))):
            if l.startswith('Concepto '):
                gridbid=a.table([['Concepto','Uds','Importe']]);bids[idx]=gridbid
            elif re.fullmatch('-+',l):
                a.nontext.append(dict(description='Printed dashed horizontal rule around table.',reference_ids=[gridbid]))
            else:
                mm=re.fullmatch(r'(.+?)\s+(\d+)\s+([\d.,]+)',l);desc,q,v=mm.groups()
                row=a.blocks[[b['id'] for b in a.blocks].index(gridbid)]['rows'];rid=f'{gridbid}-r{len(row)+1:02}'
                row.append(dict(id=rid,cells=[dict(id=f'{rid}-c{j}',column=j,row_span=1,column_span=1,text=t) for j,t in enumerate([desc,q,v],1)]))
                a.line(desc,q,v,rid)
                for j,f in enumerate(['description','quantity','amount'],1):a.link(f'/lines/{len(a.invoice["lines"])-1}/{f}',f'{rid}-c{j}')
            continue
        kind='footer' if l==FOOTER else 'heading' if idx==0 or re.match(r'(FACTURA|Invoice #|Nº de factura|REF FACTURA)',l) else 'paragraph'
        if l.startswith(('Condiciones de pago:','Los sistemas automaticos','Domicilio social')): kind='note'
        bid=a.block(l,kind);bids[idx]=bid
        if kind in ['footer','note']:a.annotation(l,kind,bid)
        if l.startswith('Condiciones de pago:'):
            raw=l.split(': ',1)[1].split('. ',1)[0]+'.'
            a.invoice['additional_fields'].append(dict(label='Condiciones de pago',raw_value=raw,normalized_value=None))
            a.link('/additional_fields/0/label',bid);a.link('/additional_fields/0/raw_value',bid)
    inv=a.invoice
    if typ=='simple':
        a.put('/supplier/name',lines[3],bids[3]);supplier_idx=4
    elif typ=='grid':
        ii=next(i for i,l in enumerate(lines) if l.startswith('Emisor:'))
        mm=re.fullmatch(r'Emisor: (.+?)\s+· NIF (\S+)\s+· (.+)',lines[ii]);name,tax,loc=mm.groups()
        for field,v in [('name',name),('tax_id',tax),('location',loc)]:a.put('/supplier/'+field,v,bids[ii])
    else:a.put('/supplier/name',lines[0],bids[0])
    for idx,l in enumerate(lines):
        if idx not in bids: continue
        bid=bids[idx]
        if re.search(r'\bNIF[: ]',l) and typ!='grid':
            tax=re.search(r'\bNIF:? ([A-Z]\d+)',l)
            if tax:a.put('/supplier/tax_id',tax[1],bid)
            if ' | ' in l: a.put('/supplier/location',l.split('|')[1].strip(),bid)
            elif ' · NIF ' in l:a.put('/supplier/location',l.split(' · NIF ')[0],bid)
            elif typ=='mono':a.put('/supplier/location',l.split()[-1],bid)
        if l.endswith('· España'):a.put('/supplier/location',l,bid)
        ib=re.search(r'IBAN\)?: (ES[\d ]+)',l)
        if ib:a.put('/payment/iban',ib[1].replace(' ',''),bid)
        num=re.search(r'(?:Factura: |REF FACTURA: |Nº de factura: |FACTURA Nº: |Invoice # |FACTURA SIMPLIFICADA Nº )([^ ]+)',l)
        if num:a.put('/invoice_number',num[1],bid)
        dat=re.search(r'(?:Fecha: |FECHA: |Fecha factura: )(\d+/\d+/\d+)',l)
        if dat:a.date(dat[1],bid)
        if l.startswith('Fecha de emisión: '):a.date(l.split(': ',1)[1],bid)
        po=re.search(r'PO-\d+-\d+',l)
        if po:a.put('/purchase_order_reference',po[0],bid)
        if l.startswith(('Cliente:','CLIENTE:','Facturar a:','Bill to:','Destinatario:')):
            customer=re.search(r'^(?:Cliente:|CLIENTE:|Facturar a:|Bill to:|Destinatario:) (.+?)\s+(?:· |— |\()?CIF:? ([A-Z]\d+)',l)
            if not customer:raise ValueError(l)
            a.put('/customer/name',customer[1].strip(),bid);a.put('/customer/tax_id',customer[2],bid)
        if l.startswith('Paseo de la Castellana'):
            a.put('/customer/address',l,bid);a.put('/customer/location','Madrid',bid)
        if '€' in l or 'EUR' in l:a.put('/currency','EUR',bid)
        if re.match(r'(Base:|Base imponible:|BASE IMPONIBLE|Importe base:|Subtotal:)',l):
            a.put('/totals/taxable_base',amount(re.findall(r'\d[\d.,]*',l)[-1]),bid)
        if re.match(r'(TOTAL|Total factura:|IMPORTE TOTAL:)',l):a.put('/totals/total',amount(re.findall(r'\d[\d.,]*',l)[-1]),bid)
        if re.match(r'(IVA |I.V.A. |Cuota IVA )',l):
            label=l.split(' (')[0]; vals=re.findall(r'\d[\d.,]*',l)
            inv['taxes'].append(dict(label=label,rate_percent=vals[0],amount=amount(vals[-1])))
            for f in ['label','rate_percent','amount']:a.link('/taxes/0/'+f,bid)
        match=None
        if typ=='simple':match=re.fullmatch(r'(.+?) \.{7} ([\d.,]+)',l)
        elif typ=='standard':match=re.fullmatch(r'(.+?) x(\d+)\s+\.\.\. ([\d.,]+) €',l)
        elif typ=='longdate':match=re.fullmatch(r'(.+?) \((\d+)\) — ([\d.,]+) €',l)
        elif typ=='english':match=re.fullmatch(r'- (.+?) \((\d+) ud\): EUR ([\d.,]+)',l)
        elif typ=='mono' and not re.match(r'(BASE|IVA|TOTAL|NIF|CUENTA|CLIENTE|FECHA|PEDIDO|REF)',l):match=re.fullmatch(r'(.+?)\s{2,}([\d.,]+)',l)
        if match:
            vals=match.groups();a.line(vals[0],vals[1] if len(vals)==3 else None,vals[-1],bid)
    if typ=='mono':a.unheaded_table()
    a.save()

def scan(entry,s):
    a=Annotation(entry); small=s['n']<=16
    def b(text,kind='paragraph',uncertain=None):return a.block(text,kind,uncertain)
    bid=b(s['name'],'heading','Damaged accented glyphs; replacement symbol denotes unreadable glyph, not literal text.' if '�' in s['name'] else None)
    if '�' in s['name']:
        a.issue('/supplier/name',s['name'],['Mensajería Rápida del Sur S.L.'],'unreadable');a.link('/supplier/name',bid)
    else:a.put('/supplier/name',s['name'],bid)
    if small:
        bid=b(f"NIF: {s['tax']}  IBAN: {s['iban']}")
        a.put('/supplier/tax_id',s['tax'],bid);a.put('/payment/iban',s['iban'].replace(' ',''),bid)
        bid=b(f"Factura {s['number']}  Fecha {s['date']}");a.put('/invoice_number',s['number'],bid);a.date(s['date'],bid)
        bid=b('Pedido '+s['po']);a.put('/purchase_order_reference',s['po'],bid)
    else:
        bid=b(f"NIF: {s['tax'] or ''}  ·  {s['location']}",uncertain='Black scan band obscures NIF; do not reconstruct from another document.' if s['tax'] is None else None)
        a.put('/supplier/tax_id',s['tax'],bid);a.put('/supplier/location',s['location'],bid)
        if s['tax'] is None:a.issue('/supplier/tax_id',None,[],'unreadable')
        bid=b('Cuenta de abono (IBAN): '+s['iban']);a.put('/payment/iban',s['iban'].replace(' ',''),bid)
        bid=b('FACTURA Nº '+(s['number'] or ''),'heading','Invoice number covered by black band.' if s['number'] is None else None)
        a.put('/invoice_number',s['number'],bid)
        if s['number'] is None:a.issue('/invoice_number',None,[],'unreadable')
        bid=b('Fecha: '+s['date']);a.date(s['date'],bid)
        bid=b('Pedido: '+s['po']);a.put('/purchase_order_reference',s['po'],bid)
    customer='Cliente: Banco Miralmar S.A.  CIF A58231074' if small else 'Cliente: Banco Miralmar S.A.  ·  CIF: A58231074'
    bid=b(customer);a.put('/customer/name','Banco Miralmar S.A.',bid);a.put('/customer/tax_id','A58231074',bid)
    for desc,val in s['lines']:
        bid=b(desc+'  '+val,uncertain='Damaged accented glyph; replacement symbol denotes unreadable glyph.' if '�' in desc else None)
        a.line(desc,None,val,bid)
        if '�' in desc:a.issue(f'/lines/{len(a.invoice["lines"])-1}/description',desc,[desc.replace('�','ó')],'unreadable')
    if small:
        bid=b(f"Base {s['base']}  IVA 21% {s['tax_amount']}");a.put('/totals/taxable_base',amount(s['base']),bid);taxbid=bid
    else:
        bid=b('Base imponible  '+s['base']);a.put('/totals/taxable_base',amount(s['base']),bid);taxbid=b('IVA 21%  '+s['tax_amount'])
    a.invoice['taxes']=[dict(label='IVA',rate_percent='21',amount=amount(s['tax_amount']))]
    for f in ['label','rate_percent','amount']:a.link('/taxes/0/'+f,taxbid)
    bid=b('TOTAL '+s['total']+' EUR');a.put('/totals/total',amount(s['total']),bid);a.put('/currency','EUR',bid)
    # Reading order: foreground body, notes/stamps, foreground footer, mirrored background.
    if not small:
        bid=b(FOOTER,'footer','Footer too blurred to verify every letter; tentative transcription only.' if s['n'] in [17,23] else None)
        a.annotation(FOOTER,'footer',bid)
    for field,kind in [('note','note'),('stamp','stamp'),('background','other')]:
        if field in s:
            bid=b(s[field],kind,'Faint mirrored bleed-through; tentative partial transcription, unreadable regions remain.' if field=='background' else None)
            a.annotation(s[field],kind,bid)
            if field=='background':a.issue(f'/annotations/{len(a.invoice["annotations"])-1}/text',s[field],[],'unreadable')
    a.nontext.append(dict(description=s.get('visual','Small skewed raster text on an otherwise blank page; some accented glyphs damaged.'),reference_ids=[]))
    for ptr in s.get('uncertain',[]):
        from benchmark.core import pointer
        value=pointer(a.invoice,ptr)
        a.issue(ptr,str(value) if value is not None else None,[str(value)] if value is not None else [],'unreadable')
        # Scalars may abstain. Required descriptions/annotation text retain explicitly uncertain draft transcription.
        if not ptr.startswith('/annotations') and value is not None:a.put(ptr,None,a.links[ptr][0]['reference_ids'][0])
        ids=a.links.get(ptr,[{'reference_ids':[]}])[0]['reference_ids']
        for block in a.blocks:
            if block['id'] in ids and not block['uncertainties']:block['uncertainties']=[dict(kind='uncertain',raw_text=block['text'],description='Blur or smudge prevents certain reading; see structured issue.')]
    if not small:a.unheaded_table()
    # Foreground text and notes precede its footer; secondary mirrored material follows.
    a.blocks.sort(key=lambda b: 2 if b['kind']=='other' else 1 if b['kind']=='footer' else 0)
    a.notes.append('Every region inspected visually. No values borrowed from other invoices or filenames. U+FFFD marks a damaged glyph explicitly described in uncertainties.')
    a.save()

if __name__=='__main__':
    entries=load(BASE/'manifest.json')['documents'];scans={f"scan_{s['n']:03}.pdf":s for s in load(BASE/'review/annotation-source/scans.json')}
    for entry in entries:
        pp=paths(BASE,entry['file_id'])
        if pp['provenance'].exists():
            previous=load(pp['provenance'])
            if any(previous[stage]['status']=='human_verified' or previous[stage]['artifact_sha256']!=file_hash(pp[stage]) for stage in ['stage2','stage3']):
                raise SystemExit('Refusing to overwrite reviewed or manually changed annotations')
    for entry in entries:
        if entry['file_id'] in scans:scan(entry,scans[entry['file_id']])
        else:digital(entry)
    print('Wrote 50 draft readings, 50 draft invoices, 50 provenance records. Zero human verified.')
