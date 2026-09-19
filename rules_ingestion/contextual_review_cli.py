from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ingestion.contracts import canonical_bytes

from .contextual_fixtures import FIXTURES, REVIEWED_AT, FixtureProvider, build_fixture
from .contextual_review import review_evaluation


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline synthetic contextual review; never authorizes payment")
    parser.add_argument("--fixture", required=True, choices=FIXTURES)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        fixture = build_fixture(args.fixture)
        review = asyncio.run(review_evaluation(
            fixture.evaluation, fixture.context, fixture.artifacts,
            FixtureProvider(fixture.response), reviewed_at=REVIEWED_AT))
        export = {"synthetic": True, "fixture": args.fixture, "evaluation": fixture.evaluation,
                  "context": fixture.context,
                  "artifacts": {key: json.loads(value) for key, value in fixture.artifacts.items()},
                  "review": review}
        payload = canonical_bytes(export) + b"\n"
        with args.output.open("xb") as handle:
            handle.write(payload)
        print(json.dumps({"synthetic": True, "fixture": args.fixture,
                          "evaluation_id": review["evaluation_id"], "review_id": review["review_id"],
                          "status": review["status"], "attention_required": review["attention_required"],
                          "payment_authorized": False}))
        return 0 if review["status"] != "FAILED" else 1
    except (ValueError, OSError):
        print(json.dumps({"error": {"code": "fixture_input_or_output_error"}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
