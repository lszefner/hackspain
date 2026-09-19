"""rules_ingestion · authoring-time pipeline for Alberto's payment rules.

Pipeline (authoring time only — NEVER runs during a payment decision):

    loader  -> emits schema + clean lookups + raw rule-text cells
    classify (JEV: Noul is_rule + Choice maps_to; LLM fallback)
    store   -> versioned, fully-traced ruleset  (rules.store.json)

The decision layer (separate, 100% deterministic) consumes rules.store.json.
Same INPUT -> same OUTPUT. JEV runs here, at authoring time, so that a payment
decision is a pure function of a frozen, human-auditable ruleset.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
