"""Generate offline failure-accounting examples. These are NOT provider results."""
from pathlib import Path
from benchmark.core import load,dump,key,file_hash,value_hash
from benchmark.runner import create_run,record,reading_source
from benchmark.interpretation import blank
from benchmark.reporting import score_run

BASE=Path(__file__).resolve().parents[1]

def main():
    mp=BASE/'manifest.json';m=load(mp)
    revision=value_hash({n:file_hash(BASE/n) for n in ['runner.py','adapters.py','interpretation.py']})[:8]
    run=create_run(mp,'example-all-null-'+revision,'isolated','import','SYNTHETIC-ALL-NULL-NOT-A-PROVIDER',split='all',provisional=True)
    for e in m['documents']:
        folder=BASE/'runs'/run['run_id']/key(e['file_id']);reading=reading_source(BASE,run,e)
        dump(folder/'input-reading.json',reading)
        prediction=blank(e['file_id']);dump(folder/'output.json',prediction)
        dump(folder/'raw/import/request.json',dict(synthetic_fixture=True,purpose='Test all-null baseline; no API',reading=reading))
        dump(folder/'raw/import/response.json',prediction)
        rec=record(run,e);rec.update(status='success',reading_sha256=file_hash(folder/'input-reading.json'),artifact_sha256=file_hash(folder/'output.json'),imported=True,latency_seconds=0,usage={},cost_usd='0',attempts=0)
        dump(folder/'record.json',rec)
    score_run(mp,run['run_id'],BASE/'reports/example-all-null.json')
    run=create_run(mp,'example-ocr-not-run-'+revision,'ocr','import','NOT-EXECUTED',split='all',provisional=True)
    score_run(mp,run['run_id'],BASE/'reports/example-ocr-not-run.json')
    print('Generated two explicitly synthetic/not-executed reports; no provider calls.')

if __name__=='__main__':main()
