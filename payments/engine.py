from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
from pathlib import Path

from rules_ingestion.checks import CHECKS
from rules_ingestion.normalize import payment_terms_days

from .adapter import FIELD_PATHS, adapt_invoice
from .master import _json_safe, _valid_as_of

ENGINE_VERSION = "1.0"
RESULT_SCHEMA_VERSION = "1.0"

PUBLIC_RESULT = {"NO_PAGAR": "NO PAGAR", "ESCALAR": "ESCALAR", "PAGAR": "PAGAR"}
_PRECEDENCE = ("NO_PAGAR", "ESCALAR", "PAGAR")
_VALID_ON_FAIL = {"NO_PAGAR", "ESCALAR"}
_TRUSTED_STATUSES = {"completed", "imported"}

_BOOL_PARAMS = {
    "require_active", "require_nif_in_master", "require_iban_match",
    "check_nif_control_digit", "require_erp_pending", "check_line_items_sum",
    "check_iva", "check_total_is_base_plus_iva", "check_matches_pedido",
    "allow_future", "enforce_payment_terms",
}
_DECIMAL_PARAMS = {"tolerance_eur", "escalate_above_eur"}
_STR_LIST_PARAMS = {"allowed_currencies", "required_fields"}


def _canon_action(value: str) -> str:
    return str(value).replace(" ", "_")


def _rule_id(rule: dict):
    return rule.get("id", rule.get("rule_id"))


def _normalize_on_fail(value):
    if not isinstance(value, str):
        return None
    action = _canon_action(value)
    return action if action in _VALID_ON_FAIL else None


def _validate_params(rid, params: dict) -> None:
    for key, value in params.items():
        if key in _BOOL_PARAMS and not isinstance(value, bool):
            raise ValueError(f"rule {rid} param {key} must be a boolean")
        if key in _DECIMAL_PARAMS:
            if isinstance(value, bool):
                raise ValueError(f"rule {rid} param {key} must be a number")
            try:
                parsed = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"rule {rid} param {key} must be a number") from exc
            if not parsed.is_finite() or parsed < 0:
                raise ValueError(f"rule {rid} param {key} must be finite and >= 0")
        if key == "soft_duplicate_verdict" and value not in ("NEEDS_REVIEW", "FAIL"):
            raise ValueError(
                f"rule {rid} soft_duplicate_verdict must be NEEDS_REVIEW or FAIL"
            )
        if key in _STR_LIST_PARAMS and (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise ValueError(f"rule {rid} param {key} must be nonempty strings")


class DecisionEngine:
    def __init__(self, ruleset: dict, master: dict, *,
                 schema_dir: str | Path | None = None):
        if not isinstance(ruleset, dict):
            raise TypeError("ruleset must be an object")
        schema_versions = {"2.0", "1.1"}
        version = ruleset.get("schema_version") or ruleset.get("store_schema_version")
        if version not in schema_versions:
            raise ValueError(f"unsupported ruleset schema version: {version!r}")
        rules = ruleset.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError("ruleset must contain a non-empty rules list")
        if not isinstance(ruleset.get("ruleset_version"), str):
            raise TypeError("ruleset_version must be a string")
        precedence = [_canon_action(p) for p in (ruleset.get("precedence") or [])]
        if precedence != list(_PRECEDENCE):
            raise ValueError("ruleset precedence must be NO_PAGAR, ESCALAR, PAGAR")
        seen = set()
        enabled_canonicals = set()
        normalized = []
        for rule in rules:
            if not isinstance(rule, dict):
                raise TypeError("each rule must be an object")
            rid = _rule_id(rule)
            if not isinstance(rid, str) or not rid:
                raise ValueError("rule ids must be nonempty strings")
            if rid in seen:
                raise ValueError(f"duplicate rule id {rid!r}")
            seen.add(rid)
            canonical = rule.get("canonical")
            if canonical is not None and not isinstance(canonical, str):
                raise ValueError(f"rule {rid} canonical must be a string or null")
            if not isinstance(rule.get("enabled"), bool):
                raise TypeError(f"rule {rid} enabled must be a boolean")
            params = rule.get("params")
            if params is not None and not isinstance(params, dict):
                raise ValueError(f"rule {rid} params must be an object")
            _validate_params(rid, params or {})
            on_fail = _normalize_on_fail(rule.get("on_fail"))
            if on_fail is None:
                raise ValueError(f"rule {rid} has invalid on_fail {rule.get('on_fail')!r}")
            if rule["enabled"] and canonical is not None:
                if canonical in enabled_canonicals:
                    raise ValueError(
                        f"multiple enabled rules for canonical {canonical!r}"
                    )
                enabled_canonicals.add(canonical)
            entry = dict(rule)
            entry["on_fail"] = on_fail
            normalized.append(entry)
        self.ruleset = copy.deepcopy({**ruleset, "rules": normalized})
        self.master = copy.deepcopy(master)
        self.ruleset_sha256 = self._hash(ruleset)
        self.as_of = _valid_as_of(
            self.master.get("as_of") or self.master.get("today")
        )
        if self.master.get("today") not in (None, self.as_of):
            raise ValueError("master today and as_of disagree")
        self.master["today"] = self.as_of
        self.master["as_of"] = self.as_of
        self.master_sha256 = self._hash(
            {k: v for k, v in self.master.items() if k != "sha256"}
        )
        self.master["sha256"] = self.master_sha256
        self.policy_id = ruleset.get("policy_id") or ruleset.get("policy_class")
        from ingestion.contracts import Contracts

        self.contracts = Contracts(schema_dir)

    @staticmethod
    def _hash(value) -> str:
        from ingestion.contracts import canonical_bytes, digest

        return digest(canonical_bytes(_json_safe(value)))

    @staticmethod
    def _gate_reason(record: dict) -> str | None:
        problem = record.get("input_problem")
        if problem:
            return str(problem)
        status = record.get("status")
        if status not in _TRUSTED_STATUSES:
            return f"input status {status!r} is not trusted for payment"
        invoice = record.get("invoice")
        if not isinstance(invoice, dict):
            return "record has no invoice"
        if invoice.get("file_id") != record["file_id"]:
            return "invoice file_id does not match the record"
        if record.get("error"):
            return "record carries an extraction error"
        checks = record.get("checks")
        if checks is not None:
            if not isinstance(checks, dict) or not checks or not all(
                isinstance(v, bool) for v in checks.values()
            ):
                return "extraction checks are malformed"
            if any(v is False for v in checks.values()):
                return "extraction checks failed"
        if invoice.get("issues"):
            return "invoice carries extraction uncertainty issues"
        return None

    @staticmethod
    def _condition_ok(rule: dict, canonical: str) -> str | None:
        condition = rule.get("condition")
        if condition is None:
            return None
        if not isinstance(condition, dict) or condition.get("kind") != "python_check":
            return "unsupported rule condition kind; not executed"
        if condition.get("module") != "rules_ingestion.checks":
            return "condition module is not rules_ingestion.checks; not executed"
        expected = CHECKS[canonical].__name__
        if condition.get("function") != expected:
            return f"condition function {condition.get('function')!r} != {expected}; not executed"
        return None

    def _run_rule(self, rule: dict, inv: dict, inv_master: dict) -> dict:
        rid = _rule_id(rule)
        canonical = rule.get("canonical")
        source_refs = rule.get("source_refs") or (
            (rule.get("trace") or {}).get("matched_from") or []
        )
        row = {
            "rule_id": rid,
            "canonical": canonical,
            "enabled": True,
            "params": rule.get("params") or {},
            "on_fail": rule["on_fail"],
            "source_refs": source_refs,
        }
        check = CHECKS.get(canonical)
        if check is None:
            row.update(verdict="NEEDS_REVIEW", action="ESCALAR",
                       reason="unsupported canonical rule; never executed")
            return row
        problem = self._condition_ok(rule, canonical)
        if problem:
            row.update(verdict="NEEDS_REVIEW", action="ESCALAR", reason=problem)
            return row
        verdict, reason = check(inv, inv_master, rule.get("params") or {})
        action = {"FAIL": rule["on_fail"], "NEEDS_REVIEW": "ESCALAR",
                  "PASS": "PAGAR"}[verdict]
        row.update(verdict=verdict, reason=reason, action=action)
        return row

    @staticmethod
    def _issue_row(issue: dict, index: int) -> dict:
        return {
            "rule_id": f"READINESS_{index}",
            "canonical": "READINESS",
            "enabled": True,
            "params": {},
            "on_fail": "ESCALAR",
            "source_refs": [],
            "verdict": "NEEDS_REVIEW",
            "reason": issue["reason"],
            "action": "ESCALAR",
            "code": issue.get("code"),
        }

    def _find_vendor(self, inv: dict):
        return (self.master["proveedores"].get(inv.get("vendor_id"))
                or self.master.get("proveedores_by_nif", {}).get(inv.get("nif")))

    def _reconcile(self, inv: dict, enabled_canonicals: set) -> list[dict]:
        issues = []
        enabled = self.ruleset["rules"]
        if "DATES" in enabled_canonicals:
            terms = any(
                r.get("canonical") == "DATES" and r.get("enabled")
                and (r.get("params") or {}).get("enforce_payment_terms", True)
                for r in enabled
            )
            vendor = self._find_vendor(inv)
            if terms and (
                vendor is None
                or payment_terms_days(vendor.get("condiciones")) is None
            ):
                issues.append({
                    "code": "vendor_terms_unknown",
                    "reason": "master payment terms cannot be verified",
                })
        pedido = inv.get("pedido")
        if not pedido:
            return issues
        if {"VENDOR", "AMOUNT", "DUPLICATES"} & enabled_canonicals:
            ped = self.master.get("pedidos", {}).get(pedido)
            if ped is None:
                issues.append({"code": "po_missing",
                               "reason": f"purchase order {pedido} not in master"})
            else:
                if not ped.get("nif") or not ped.get("proveedor_id"):
                    issues.append({"code": "po_identity_missing",
                                   "reason": "purchase order lacks vendor identity"})
                else:
                    if inv.get("nif") and ped["nif"] != inv["nif"]:
                        issues.append({"code": "po_nif_mismatch",
                                       "reason": "invoice NIF does not match purchase order NIF"})
                    if inv.get("vendor_id") and ped["proveedor_id"] != inv["vendor_id"]:
                        issues.append({"code": "po_vendor_mismatch",
                                       "reason": "resolved vendor does not match purchase order vendor"})
                if ped.get("importe_total") is None:
                    issues.append({"code": "po_amount_missing",
                                   "reason": "purchase order has no total amount"})
        conflict = self.master.get("erp_conflicts", {}).get(pedido)
        if conflict:
            issues.append({"code": "erp_conflict", "reason": conflict})
        if "AMOUNT" in enabled_canonicals or "DUPLICATES" in enabled_canonicals:
            if self.master.get("erp_unknown"):
                issues.append({"code": "erp_unavailable",
                               "reason": "no ERP snapshot; order state unknown"})
            else:
                rows = self.master.get("erp_rows", {}).get(pedido) or []
                if not rows:
                    issues.append({"code": "erp_row_missing",
                                   "reason": f"no ERP record for order {pedido}"})
                else:
                    tol = Decimal("0.01")
                    for rule in enabled:
                        if rule.get("canonical") == "AMOUNT" and rule.get("enabled"):
                            value = (rule.get("params") or {}).get("tolerance_eur", 0.01)
                            tol = Decimal(str(value))
                    matched = False
                    for row in rows:
                        if not row.get("nif") or not row.get("proveedor"):
                            continue
                        id_ok = (
                            inv.get("nif") is not None and row["nif"] == inv["nif"]
                            and inv.get("vendor_id") is not None
                            and row["proveedor"] == inv["vendor_id"]
                        )
                        amt_ok = row["importe"] is not None \
                            and inv.get("total") is not None \
                            and abs(row["importe"] - inv["total"]) <= tol
                        if id_ok and amt_ok:
                            matched = True
                    if not matched:
                        issues.append({
                            "code": "erp_row_mismatch",
                            "reason": "no ERP record matches invoice vendor and amount",
                        })
        return issues

    @staticmethod
    def _invoice_hash(invoice) -> str | None:
        if not isinstance(invoice, dict):
            return None
        from ingestion.contracts import canonical_bytes, digest

        try:
            return digest(canonical_bytes(_json_safe(invoice)))
        except (TypeError, ValueError):
            return None

    def decide(self, records: list[dict]) -> list[dict]:
        if not isinstance(records, list):
            raise TypeError("records must be a list")
        seen = set()
        for record in records:
            if not isinstance(record, dict):
                raise TypeError("each record must be an object")
            fid = record.get("file_id")
            if not isinstance(fid, str) or not fid:
                raise ValueError("each record needs a non-empty file_id")
            if fid in seen:
                raise ValueError(f"duplicate record file_id {fid!r}")
            seen.add(fid)

        enabled_rules = [r for r in self.ruleset["rules"] if r["enabled"]]
        enabled_canonicals = {r.get("canonical") for r in enabled_rules}

        gated, trusted = {}, {}
        for record in records:
            fid = record["file_id"]
            reason = self._gate_reason(record)
            if reason is not None:
                gated[fid] = reason
                continue
            try:
                invoice = copy.deepcopy(record["invoice"])
                inv, issues = adapt_invoice(invoice, self.master, self.contracts)
            except (ValueError, KeyError, TypeError) as exc:
                gated[fid] = f"invoice failed schema or adaptation: {exc}"
                continue
            trusted[fid] = (record, inv, issues)

        hard_groups: dict[tuple, list[str]] = {}
        soft_groups: dict[tuple, list[str]] = {}
        order_groups: dict[str, list[str]] = {}
        for fid, (_, inv, issues) in trusted.items():
            if issues:
                continue
            hard_key = (inv.get("invoice_number"), inv.get("vendor_id"))
            if all(hard_key):
                hard_groups.setdefault(hard_key, []).append(fid)
            soft_key = (inv.get("total"), inv.get("date"))
            if all(x is not None for x in soft_key):
                soft_groups.setdefault(soft_key, []).append(fid)
            if inv.get("pedido"):
                order_groups.setdefault(inv["pedido"], []).append(fid)

        results = []
        for record in sorted(records, key=lambda r: r["file_id"]):
            fid = record["file_id"]
            base = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "file_id": fid,
                "ruleset_version": self.ruleset["ruleset_version"],
                "policy_id": self.policy_id,
                "as_of": self.as_of,
                "engine_version": ENGINE_VERSION,
                "ruleset_sha256": self.ruleset_sha256,
                "master_sha256": self.master_sha256,
                "source_sha256": record.get("source_sha256"),
                "input_status": record.get("status"),
                "artifacts": record.get("artifacts"),
                "artifact_hashes": record.get("artifact_hashes"),
            }
            if fid in gated:
                reason = gated[fid]
                check = {
                    "rule_id": "INPUT_GATE",
                    "canonical": "INPUT_GATE",
                    "enabled": True,
                    "params": {},
                    "on_fail": "ESCALAR",
                    "source_refs": [],
                    "verdict": "NEEDS_REVIEW",
                    "reason": reason,
                    "action": "ESCALAR",
                }
                results.append({**base, "result": "ESCALAR",
                                "invoice_sha256": self._invoice_hash(record.get("invoice")),
                                "normalized_invoice": None,
                                "field_sources": None,
                                "checks": [check]})
                continue

            record, inv, issues = trusted[fid]
            inv_master = dict(self.master)
            inv_master["seen_invoice_keys"] = set(
                self.master.get("seen_invoice_keys") or set()
            )
            inv_master["seen_amount_date"] = set(
                self.master.get("seen_amount_date") or set()
            )
            inv_master["seen_order_keys"] = set(
                self.master.get("seen_order_keys") or set()
            )
            pedido = inv.get("pedido")
            if pedido and len(order_groups.get(pedido, [])) > 1 \
                    and fid in order_groups[pedido]:
                inv_master["seen_order_keys"].add(pedido)
            hard_key = (inv.get("invoice_number"), inv.get("vendor_id"))
            if all(hard_key) and len(hard_groups.get(hard_key, [])) > 1:
                inv_master["seen_invoice_keys"].add(hard_key)
            soft_key = (inv.get("total"), inv.get("date"))
            if all(x is not None for x in soft_key):
                members = soft_groups.get(soft_key, [])
                if len(members) > 1 and fid in members:
                    inv_master["seen_amount_date"].add(soft_key)

            checks = []
            for i, issue in enumerate(issues):
                checks.append(self._issue_row(issue, i))
            for i, issue in enumerate(
                self._reconcile(inv, enabled_canonicals)
            ):
                checks.append(self._issue_row(issue, len(issues) + i))
            for rule in enabled_rules:
                checks.append(self._run_rule(rule, inv, inv_master))
            if not checks:
                checks.append({
                    "rule_id": "POLICY",
                    "canonical": "POLICY",
                    "enabled": True,
                    "params": {},
                    "on_fail": "ESCALAR",
                    "source_refs": [],
                    "verdict": "NEEDS_REVIEW",
                    "reason": "no enabled checks in ruleset",
                    "action": "ESCALAR",
                })
            actions = {row["action"] for row in checks}
            internal = next(a for a in _PRECEDENCE if a in actions)
            results.append({
                **base,
                "result": PUBLIC_RESULT[internal],
                "invoice_sha256": self._invoice_hash(record["invoice"]),
                "normalized_invoice": _json_safe(inv),
                "field_sources": dict(FIELD_PATHS),
                "checks": checks,
            })
        return results
