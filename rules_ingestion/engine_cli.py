from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
from pathlib import Path

from httpx import HTTPError
from jsonschema import ValidationError
from psycopg import Error as DatabaseError

from ingestion.contracts import canonical_bytes

from .contextual_provider import review_provider_from_environment
from .decision_context import SourceSnapshot
from .engine import InvoiceDecisionEngine, RuleSource
from .engine_storage import ArchiveError


def _snapshots(path: str) -> dict[str, SourceSnapshot]:
    specs = json.loads(Path(path).read_bytes())
    result = {}
    for name, spec in specs.items():
        values = dict(spec)
        payload_file = values.pop('payload_file', None)
        if payload_file is not None:
            values['payload'] = Path(payload_file).read_bytes()
        values['authoritative_for'] = tuple(values.get('authoritative_for', []))
        result[name] = SourceSnapshot(**values)
    return result


def _rule_source(value: str) -> RuleSource:
    name, separator, path = value.partition('=')
    if not separator or not name or not path:
        raise ValueError('rule source must be NAME=PATH')
    return RuleSource(name, Path(path).read_bytes(), mimetypes.guess_type(path)[0] or 'application/octet-stream')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m rules_ingestion.engine_cli')
    commands = parser.add_subparsers(dest='command', required=True)
    evaluate = commands.add_parser('evaluate')
    evaluate.add_argument('--input-id', required=True)
    evaluate.add_argument('--interpreter', choices=('deepseek', 'jev'), required=True)
    evaluate.add_argument('--ruleset', required=True)
    evaluate.add_argument('--rule-source', action='append', required=True)
    evaluate.add_argument('--snapshots', required=True)
    evaluate.add_argument('--evaluation-date', required=True)
    evaluate.add_argument('--captured-at', required=True)
    review = commands.add_parser('review')
    review.add_argument('--record-id', required=True)
    review.add_argument('--request-key', required=True)
    review.add_argument('--reviewed-at', required=True)
    review.add_argument('--endpoint')
    review.add_argument('--model')
    show = commands.add_parser('show')
    show.add_argument('--record-id', required=True)
    args = parser.parse_args(argv)
    engine = None
    try:
        values = None
        provider = None
        if args.command == 'evaluate':
            values = {'input_id': args.input_id, 'interpreter': args.interpreter,
                      'ruleset': Path(args.ruleset).read_bytes(),
                      'rule_sources': [_rule_source(value) for value in args.rule_source],
                      'snapshots': _snapshots(args.snapshots),
                      'evaluation_date': args.evaluation_date, 'captured_at': args.captured_at}
        elif args.command == 'review':
            provider = review_provider_from_environment(endpoint=args.endpoint,
                                                        model=args.model)
        engine = InvoiceDecisionEngine.from_supabase()
        if args.command == 'evaluate':
            packet = engine.evaluate(**values)
        elif args.command == 'review':
            packet = asyncio.run(engine.review(args.record_id, provider=provider,
                                               request_key=args.request_key, reviewed_at=args.reviewed_at))
        else:
            packet = engine.load(args.record_id)
        sys.stdout.write(canonical_bytes(packet).decode() + '\n')
        if args.command == 'review':
            review_result = json.loads(engine.archive.get(packet['review']))
            return 1 if review_result['status'] == 'FAILED' else 0
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, ArchiveError,
            HTTPError, DatabaseError, ValidationError) as exc:
        sys.stderr.write(f'core_engine_failed: {type(exc).__name__}; check explicit inputs, credentials and migrations\n')
        return 1
    finally:
        if engine is not None:
            engine.close()


if __name__ == '__main__':
    raise SystemExit(main())
