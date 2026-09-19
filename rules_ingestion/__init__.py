"""rules_ingestion · authoring-time pipeline + executable canonical checks.

Pipeline (authoring time only — NEVER runs during a payment decision):

    loader  -> emits schema + clean lookups + raw rule-text cells
    classify (JEV: Noul is_rule + Choice maps_to; LLM fallback)
    store   -> single outcome  (outcome/<ver>/rules.json)

Executable checks (decision-time pure functions) live in the same package:
    checks.py, invoice.py, codegen.py

The decision layer consumes outcome/<ver>/rules.json + checks.
Same INPUT -> same OUTPUT.
"""

__all__ = [
    "__version__",
    "normalize_invoice",
    "build_master",
    "CHECKS",
    "run_checks",
]
__version__ = "0.1.0"

from .invoice import normalize_invoice, build_master
from .checks import CHECKS, run_checks
