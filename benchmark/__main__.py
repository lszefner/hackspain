import argparse
from pathlib import Path
from .core import *
from .runner import create_run,execute,import_output,get_run
from .reporting import score_run,write_markdown
from .review_packet import packet,render,attest
from .adapters import catalogue,redact

def main():
    parser=argparse.ArgumentParser(description='Invoice benchmark; offline scoring never calls APIs.')
    parser.add_argument('--manifest',default='benchmark/manifest.json')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('validate');sub.add_parser('review-packet');sub.add_parser('render')
    p=sub.add_parser('validate-artifact');p.add_argument('schema',choices=['invoice','reading','manifest','provenance','run','record']);p.add_argument('path')
    p=sub.add_parser('init-run');p.add_argument('run_id');p.add_argument('--track',required=True,choices=['ocr','isolated','end_to_end']);p.add_argument('--provider',required=True,choices=['fal','jev','deepseek','helmcode','import']);p.add_argument('--model',required=True);p.add_argument('--split',choices=['development','held_out','all'],default='development');p.add_argument('--reading-run');p.add_argument('--provisional',action='store_true')
    p=sub.add_parser('run');p.add_argument('run_id');p.add_argument('--retry-failed',action='store_true');p.add_argument('--max-attempts',type=int,default=2);p.add_argument('--limit',type=int)
    p=sub.add_parser('import-output');p.add_argument('run_id');p.add_argument('--file-id',required=True);p.add_argument('--output',required=True);p.add_argument('--raw-request',required=True);p.add_argument('--raw-response',required=True);p.add_argument('--metadata',required=True)
    p=sub.add_parser('record-failure');p.add_argument('run_id');p.add_argument('--file-id',required=True);p.add_argument('--reason',required=True)
    p=sub.add_parser('score');p.add_argument('run_id');p.add_argument('--output')
    p=sub.add_parser('report');p.add_argument('json');p.add_argument('--output',required=True)
    p=sub.add_parser('catalogue');p.add_argument('--provider',required=True,choices=['jev','helmcode','deepseek'])
    p=sub.add_parser('attest-review');p.add_argument('--file-id',required=True);p.add_argument('--stage',choices=['stage2','stage3'],required=True);p.add_argument('--reviewer',required=True);p.add_argument('--attestation',required=True)
    p=sub.add_parser('refresh-draft');p.add_argument('--file-id',required=True);p.add_argument('--stage',choices=['stage2','stage3'],required=True)
    args=parser.parse_args();mp=Path(args.manifest).resolve();base=mp.parent
    if args.command=='validate':
        m=manifest(mp);reviewed=0
        for e in m['documents']:
            _,_,p=reference(base,e);reviewed+=p['stage2']['status']==p['stage3']['status']=='human_verified'
        print(f'Validated {len(m["documents"])} reference pairs; {reviewed} fully human-reviewed.')
    elif args.command=='validate-artifact':validate(args.schema,load(args.path));print('Valid')
    elif args.command=='render':render(mp);print('Rendered selected PDFs')
    elif args.command=='review-packet':print('Documents, unresolved/review notes:',packet(mp))
    elif args.command=='init-run':
        r=create_run(mp,args.run_id,args.track,args.provider,args.model,args.split,args.reading_run,args.provisional);print('Run ready:',r['run_id'],len(r['documents']))
    elif args.command=='run':execute(mp,args.run_id,args.retry_failed,args.max_attempts,args.limit)
    elif args.command=='import-output':import_output(mp,args.run_id,args.file_id,args.output,args.raw_request,args.raw_response,args.metadata)
    elif args.command=='record-failure':
        _,r,rd=get_run(mp,args.run_id)
        if args.file_id not in r['documents']:raise ValueError('File not in run')
        path=rd/key(args.file_id)/'record.json';record=load(path)
        if record['status']=='success':raise ValueError('Completed output immutable')
        record.update(status='failed',error=redact(args.reason));dump(path,record)
    elif args.command=='score':
        out=args.output or str(base/'reports'/(args.run_id+'.json'));report=score_run(mp,args.run_id,out)
        print(f'Report: {out}; reviewed coverage {len(report["reviewed_subset"])}/{report["selected_documents"]}')
    elif args.command=='report':write_markdown(load(args.json),args.output);print(args.output)
    elif args.command=='catalogue':
        out=base/'providers'/(args.provider+'-catalogue.json');catalogue(args.provider,out);print('Saved authenticated catalogue:',out)
    elif args.command=='attest-review':attest(mp,args.file_id,args.stage,args.reviewer,args.attestation)
    elif args.command=='refresh-draft':
        pp=paths(base,args.file_id);p=load(pp['provenance']);validate('reading' if args.stage=='stage2' else 'invoice',load(pp[args.stage]));p[args.stage].update(status='draft',reviewer=None,reviewed_at=None,artifact_sha256=file_hash(pp[args.stage]));dump(pp['provenance'],p);packet(mp)

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,KeyError) as ex:
        # No raw provider response or credentials in CLI errors.
        raise SystemExit(redact(type(ex).__name__+': '+str(ex))) from None
