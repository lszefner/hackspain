# Policy profiles for ruleset v3.
#
# Each YAML is a full decision posture: which rules are on, their params,
# and a `metrics` block that fingerprints how aggressive the policy is.
# The decision engine / UI will later read `metrics` without re-deriving
# them from params — keep both in sync when you edit a profile.
#
# Build:
#   make rules POLICY=balanced
#   make rules POLICY=strict
#   make rules POLICY=conservative
#
# Output:
#   outcome/v3/<policy>/rules.json

# ---------------------------------------------------------------------------
# Shared metric keys (documented here; each profile fills values)
# ---------------------------------------------------------------------------
# risk_appetite            0..1  higher = more willing to PAGAR / hard-fail
# escalation_bias          0..1  higher = prefer ESCALAR over auto NO_PAGAR/PAGAR
# authorization_threshold_eur    amount above which human approval is required
#   (null = authorization rule disabled)
# iban_mismatch_on_fail    ESCALAR | NO_PAGAR
# soft_duplicate_verdict   NEEDS_REVIEW | FAIL   (amount+date collision)
# amount_tolerance_eur     float
# require_erp_pending      bool
# enforce_payment_terms    bool
# check_nif_control_digit  bool
# enable_authorization     bool
# enable_new_rules_default bool  (discovered NEW start enabled?)
# policy_class             balanced | strict | conservative
