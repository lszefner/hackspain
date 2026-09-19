"""Measure read-only HTTP queries against an already configured local API.

No dotenv, model calls, processing or writes. Timings are environment-specific.
"""
import argparse
import json
import statistics
import time
from urllib.parse import quote, urlsplit
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8010')
    parser.add_argument('--samples', type=int, default=3)
    args = parser.parse_args()
    if urlsplit(args.base).hostname not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Use a local backend; this benchmark never targets shared endpoints directly.')
    if not 1 <= args.samples <= 10:
        parser.error('samples must be between 1 and 10')
    with urlopen(args.base+'/api/ui/invoices?lifecycle=processed&limit=1', timeout=60) as response:
        rows = json.load(response)['rows']
    if not rows:
        parser.error('No processed invoice available for comparison')
    file_id = quote(rows[0]['file_id'], safe='')
    results = {}
    for name, path in [('legacy_detail', '/api/factura/'+file_id),
                       ('ui_detail', '/api/ui/invoice?file='+file_id),
                       ('ui_suppliers', '/api/ui/suppliers?lifecycle=processed')]:
        times, sizes = [], []
        for _ in range(args.samples):
            started = time.perf_counter()
            with urlopen(args.base+path, timeout=60) as response:
                body = response.read()
            times.append(round((time.perf_counter()-started)*1000, 2))
            sizes.append(len(body))
        results[name] = {'ms': times, 'median_ms': statistics.median(times), 'bytes': sizes}
    results['detail_speedup'] = round(results['legacy_detail']['median_ms']/results['ui_detail']['median_ms'], 2)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
