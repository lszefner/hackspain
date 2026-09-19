"""rules_ingestion.eval · full-pipeline fixtures + scoring.

Ground truth lives in `fixtures/` (baseline, traps, structural).
Run: `python3 -m rules_ingestion.eval.score_pipeline`
"""

from __future__ import annotations

import os

__all__ = ["__version__", "fixtures_dir", "reports_dir"]
__version__ = "0.1.0"

_HERE = os.path.dirname(os.path.abspath(__file__))


def fixtures_dir() -> str:
    return os.path.join(_HERE, "fixtures")


def reports_dir() -> str:
    return os.path.join(_HERE, "reports")
