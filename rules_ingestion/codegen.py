"""codegen.py · turn a rule's TEXT into an executable Python condition via DeepSeek.

The LLM runs at AUTHORING time only. It emits a deterministic
`check(inv, master, params)` function which is then frozen into the ruleset and
executed deterministically for every payment decision — same invoice -> same
verdict, forever, regardless of the LLM. This is how a NEW rule (one with no
hand-written canonical check) still becomes runnable code instead of a TODO.

Safety: generated source is AST-validated (no imports, no eval/exec/open, no
dunder access) BEFORE it is accepted, then smoke-run once against a dummy
invoice. Anything that fails validation is returned with valid=False and never
presented as runnable.
"""
from __future__ import annotations

import ast
import json
import re
import urllib.request
from decimal import Decimal
from typing import Optional

from . import condition_authoring, conditions, helmcode

# The exact field contracts the generated code may read (matches invoice.py).
_INVOICE_FIELDS = ["invoice_number", "vendor_id", "nif", "iban", "pedido",
                   "base", "iva", "total", "currency", "date", "line_items"]
_MASTER_FIELDS = ["proveedores", "proveedores_by_nif", "pedidos", "erp_estado",
                  "seen_invoice_keys", "seen_amount_date", "today"]

_FORBIDDEN_CALLS = {"eval", "exec", "open", "compile", "__import__", "input",
                    "globals", "locals", "vars", "getattr", "setattr", "delattr"}


# --------------------------------------------------------------------------- #
# DeepSeek call (OpenAI-compatible, via HelmCode, stdlib HTTP)
# --------------------------------------------------------------------------- #
def _deepseek_chat(prompt: str, max_tokens: int = 700) -> Optional[str]:
    key, base, model = (helmcode.api_key(), helmcode.chat_endpoint(),
                        helmcode.authoring_model())
    if not (key and base and model):
        return None
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": max_tokens}
    req = urllib.request.Request(
        base, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) rules_ingestion/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def _prompt(rule_text: str) -> str:
    return (
        "You are a DETERMINISTIC Python code generator for vendor-payment rules. Given ONE rule in "
        "natural language, output EXACTLY one function and nothing else:\n\n"
        "def check(inv, master, params):\n"
        "    # ...\n"
        "    return verdict, reason   # verdict ∈ {'PASS', 'FAIL', 'NEEDS_REVIEW'}\n\n"
        "DATA CONTRACT (use ONLY these keys, always via .get(), never direct indexing):\n"
        f"  inv:    {_INVOICE_FIELDS}\n"
        f"  master: {_MASTER_FIELDS}\n\n"
        "TYPE NOTES:\n"
        "- inv['base'|'iva'|'total'] and master['pedidos'][x]['importe_total'] are Decimal "
        "(already available in scope — do not import decimal).\n"
        "- inv['date'] and master['today'] are strings in 'YYYY-MM-DD' format. Compare them as strings "
        "or convert via a plain string split — do not import datetime.\n"
        "- Any key may be absent — always use .get(key) with a sensible default, never inv[key].\n\n"
        "VERDICT SEMANTICS:\n"
        "- FAIL: the rule's condition is checked and VIOLATED.\n"
        "- NEEDS_REVIEW: the rule cannot be conclusively evaluated from available data, or the rule "
        "itself states that a human must judge (thresholds, anomalies, ambiguous cases).\n"
        "- PASS: the condition is satisfied, OR the rule does not apply to this invoice at all "
        "(e.g. a vendor-type-specific rule when inv/master shows a different vendor type).\n"
        "- `reason` must be a short human-readable string naming the specific value(s) that drove the "
        "verdict (e.g. \"IBAN mismatch: invoice ES91... vs master ES76...\", not just \"failed check\").\n\n"
        "HARD PROHIBITIONS (code containing any of these must not be produced — regenerate instead):\n"
        "  import, eval, exec, open, __ (dunder / underscore attribute access), any network or file I/O, "
        "any name not defined by the data contract above, any control-flow that can raise an unhandled "
        "exception on missing keys (always .get() with defaults, wrap risky comparisons defensively).\n\n"
        "OUTPUT FORMAT:\n"
        "- Return ONLY the function code. No markdown fences, no comments-as-explanation before/after "
        "the function, no example usage, no print statements.\n"
        "- The function must be syntactically complete and independently runnable given the contract above.\n\n"
        "SELF-CHECK before returning code (do silently):\n"
        "1. Does every inv/master access use .get()?\n"
        "2. Does the function handle missing keys (return NEEDS_REVIEW rather than crash)?\n"
        "3. Does `reason` cite actual values rather than a generic message?\n"
        "4. Is there anything in the prohibited list above anywhere in the output? If so, remove it.\n\n"
        "EXAMPLE\n"
        'Rule: "IBAN debe coincidir con el maestro"\n\n'
        "def check(inv, master, params):\n"
        "    inv_iban = inv.get('iban')\n"
        "    vendor_nif = inv.get('nif')\n"
        "    if not inv_iban or not vendor_nif:\n"
        "        return 'NEEDS_REVIEW', 'missing IBAN or NIF on invoice'\n"
        "    vendor = master.get('proveedores_by_nif', {}).get(vendor_nif)\n"
        "    if vendor is None:\n"
        "        return 'NEEDS_REVIEW', f'vendor NIF {vendor_nif} not found in master'\n"
        "    master_iban = vendor.get('iban')\n"
        "    if not master_iban:\n"
        "        return 'NEEDS_REVIEW', f'vendor {vendor_nif} has no IBAN in master'\n"
        "    if inv_iban != master_iban:\n"
        "        return 'FAIL', f'IBAN mismatch: invoice {inv_iban} vs master {master_iban}'\n"
        "    return 'PASS', 'IBAN matches master'\n\n"
        f'Rule: "{rule_text}"\n'
    )


# --------------------------------------------------------------------------- #
# Extract + validate
# --------------------------------------------------------------------------- #
def _extract_code(raw: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
    code = m.group(1) if m else raw
    # keep from the first 'def check' onward
    idx = code.find("def check")
    return code[idx:].strip() if idx != -1 else code.strip()


def validate_source(source: str) -> "tuple[bool, str]":
    """AST-check: exactly one `def check(inv, master, params)`, no unsafe nodes."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"syntax error: {exc}"
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(funcs) != 1 or funcs[0].name != "check":
        return False, "must define exactly one function named 'check'"
    args = [a.arg for a in funcs[0].args.args]
    if args != ["inv", "master", "params"]:
        return False, f"signature must be check(inv, master, params), got {args}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "imports are not allowed"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, "dunder attribute access is not allowed"
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_CALLS:
            return False, f"use of '{node.id}' is not allowed"
    return True, "ok"


_SMOKE_INV = {
    "invoice_number": "F-1", "vendor_id": "P001", "nif": "B46102331",
    "iban": "ES2100491500051234567890", "pedido": "PO-2026-0002",
    "base": Decimal("100.00"), "iva": Decimal("21.00"), "total": Decimal("121.00"),
    "currency": "EUR", "date": "2026-05-01", "line_items": [Decimal("100.00")],
}
_SMOKE_MASTER = {
    "proveedores": {}, "proveedores_by_nif": {}, "pedidos": {},
    "erp_estado": {}, "seen_invoice_keys": set(), "seen_amount_date": set(),
    "today": "2026-09-19",
}


def smoke_run(source: str) -> "tuple[bool, str]":
    """Exec the validated source in a restricted namespace and call it once."""
    safe_builtins = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
                     for k in ("abs", "sum", "len", "min", "max", "round", "str", "int",
                               "float", "bool", "tuple", "list", "dict", "set", "sorted",
                               "any", "all", "range", "enumerate", "isinstance", "None",
                               "True", "False") if _has_builtin(k)}
    ns = {"__builtins__": safe_builtins, "Decimal": Decimal}
    try:
        exec(compile(source, "<generated>", "exec"), ns)
        verdict, reason = ns["check"](dict(_SMOKE_INV), dict(_SMOKE_MASTER), {})
        if verdict not in ("PASS", "FAIL", "NEEDS_REVIEW"):
            return False, f"invalid verdict: {verdict!r}"
        return True, f"{verdict}: {reason}"
    except Exception as exc:
        return False, f"runtime error: {type(exc).__name__}: {exc}"


def _has_builtin(name: str) -> bool:
    b = __builtins__
    return (name in b) if isinstance(b, dict) else hasattr(b, name)


# --------------------------------------------------------------------------- #
# Structured conditions (the executable form)                                  #
# --------------------------------------------------------------------------- #
# A rule only changes a decision if it compiles to `condition/1`, the schema
# rules_ingestion.conditions validates and execution_rules runs. Generation
# here produces a DRAFT -- plain field/op/value -- and condition_authoring
# turns it into the tagged, typed, validated condition. The draft vocabulary is
# closed: a field outside decision_registry is refused at authoring time
# instead of becoming an UNSUPPORTED rule at evaluation time.
_OPS = set(conditions.OPS)


def _structured_prompt(rule_text: str) -> str:
    ops = ", ".join(sorted(_OPS))
    return (
        "Translate ONE vendor-payment rule into a deterministic condition draft. Respond with ONLY "
        "the JSON object below - no markdown, no prose.\n\n"
        "OUTPUT SCHEMA (strict JSON):\n"
        "{\n"
        '  "logic": "AND" | "OR",\n'
        '  "clauses": [{"field": "<field>", "op": "<op>", "value": <literal>}],\n'
        '  "on_fail": "ESCALAR" | "NO_PAGAR",\n'
        '  "expressible": true | false,\n'
        '  "note": "<why not expressible, when expressible is false>"\n'
        "}\n\n"
        "THE CLAUSES DESCRIBE THE COMPLIANT CASE: the rule PASSES when they hold. "
        "Express \"invoices above 5000 need approval\" as total <= 5000, not total > 5000.\n\n"
        f"ALLOWED OPERATORS: {ops}\n"
        "  - `exists` / `missing` take NO value.\n"
        "  - `in` / `not_in` take a list of strings and only apply to text fields.\n\n"
        "ALLOWED FIELDS (use the exact names; never invent one, never guess a field that merely "
        "sounds right):\n"
        f"{condition_authoring.catalogue_text()}\n\n"
        "To compare two fields, write the value as {\"field\": \"<other field>\"}. Otherwise `value` "
        "is a plain JSON literal (number, string, boolean, ISO date, or list of strings).\n\n"
        "RULES FOR TRANSLATION:\n"
        "1. Use ONLY the fields above. If the rule needs a fact that is not in the list - a country, "
        "a tax regime, a withholding rate, a contract, a cost centre, an approver - then it is NOT "
        "expressible: return \"expressible\": false with a short \"note\" naming the missing fact, and "
        "an empty \"clauses\" list. A wrong field is far worse than an honest refusal.\n"
        "2. `on_fail` is the consequence the rule states: \"NO_PAGAR\" for a hard block on payment, "
        "\"ESCALAR\" when a human must judge. When the rule does not say, use \"ESCALAR\".\n"
        "3. Take the literal from the rule text. Never emit a placeholder like \"X\" or \"TBD\".\n"
        "4. Amounts are compared in EUR.\n\n"
        "EXAMPLES\n"
        'Rule: "Facturas superiores a 5.000 EUR requieren aprobacion del director financiero"\n'
        '{"logic":"AND","clauses":[{"field":"invoice.total","op":"<=","value":5000}],'
        '"on_fail":"ESCALAR","expressible":true}\n\n'
        'Rule: "El IBAN de la factura debe coincidir con el del maestro de proveedores"\n'
        '{"logic":"AND","clauses":[{"field":"invoice.iban","op":"==","value":{"field":"supplier.iban"}}],'
        '"on_fail":"ESCALAR","expressible":true}\n\n'
        'Rule: "No se paga un pedido que el ERP no marque como PENDIENTE"\n'
        '{"logic":"AND","clauses":[{"field":"erp.order_payment_status","op":"==","value":"PENDIENTE"}],'
        '"on_fail":"NO_PAGAR","expressible":true}\n\n'
        'Rule: "Toda factura debe indicar el numero de pedido"\n'
        '{"logic":"AND","clauses":[{"field":"invoice.order_reference","op":"exists"}],'
        '"on_fail":"ESCALAR","expressible":true}\n\n'
        'Rule: "Queda prohibido pagar a proveedores radicados en paraisos fiscales"\n'
        '{"logic":"AND","clauses":[],"on_fail":"NO_PAGAR","expressible":false,'
        '"note":"needs the supplier country, which no available source provides"}\n\n'
        f'Rule: "{rule_text}"\nJSON:'
    )


def compile_draft(draft: dict) -> tuple[dict | None, str | None]:
    """Turn a draft into a validated `condition/1`, or (None, reason).

    The reason is a stable code so an unexecutable rule can be reported at
    ingestion time instead of silently failing at evaluation time.
    """
    if not isinstance(draft, dict):
        return None, "INVALID_DRAFT"
    if draft.get("expressible") is False:
        note = draft.get("note")
        return None, f"NOT_EXPRESSIBLE: {note}" if note else "NOT_EXPRESSIBLE"
    raw_clauses = draft.get("clauses")
    if not isinstance(raw_clauses, list) or not raw_clauses:
        return None, "EMPTY_CONDITION"
    on_fail = draft.get("on_fail")
    if on_fail not in ("ESCALAR", "NO_PAGAR"):
        on_fail = "ESCALAR"
    clauses = []
    try:
        for entry in raw_clauses:
            if not isinstance(entry, dict):
                raise condition_authoring.AuthoringError("INVALID_CLAUSE")
            field = entry.get("field")
            op = entry.get("op")
            if op in ("exists", "missing"):
                clauses.append(condition_authoring.clause(field, op))
                continue
            value = entry.get("value")
            if isinstance(value, dict) and set(value) == {"field"}:
                clauses.append(condition_authoring.clause(
                    field, op, field_ref=value["field"]))
                continue
            if isinstance(value, dict):
                raise condition_authoring.AuthoringError("INVALID_LITERAL")
            clauses.append(condition_authoring.clause(field, op, value))
        condition = condition_authoring.build(
            clauses, logic=draft.get("logic") or "AND", on_fail=on_fail)
    except condition_authoring.AuthoringError as exc:
        return None, str(exc)
    return condition, None


# Wording that makes a rule a hard block rather than an escalation.
_HARD_BLOCK = ("no pagar", "no se paga", "no se pagara", "no abonar",
               "prohibido", "nunca se paga", "en ningun caso")


def heuristic_structured(rule_text: str) -> dict | None:
    """Offline, deterministic drafts for the shapes a norm sheet repeats.

    Only patterns expressible with registry fields are emitted. A recognised
    rule whose facts no source provides returns a draft marked not expressible,
    so the reason survives into the ruleset instead of disappearing.
    """
    from .normalize import _accent_fold

    text = _accent_fold(rule_text or "")

    def draft(clauses, on_fail="ESCALAR", logic="AND"):
        return {"logic": logic, "clauses": clauses, "on_fail": on_fail,
                "expressible": True, "compiled_by": "heuristic"}

    def refuse(note):
        return {"logic": "AND", "clauses": [], "on_fail": "ESCALAR",
                "expressible": False, "note": note, "compiled_by": "heuristic"}

    # Facts the field registry cannot supply. Named explicitly so the ruleset
    # records WHY the rule is not executable.
    for probe, note in (
        ("paraiso", "supplier country / tax-haven list is not an available field"),
        ("seguridad social", "social-security standing is not an available field"),
        ("tgss", "social-security standing is not an available field"),
        ("irpf", "withholding rate is not an available field"),
        ("retencion", "withholding rate is not an available field"),
    ):
        if probe in text:
            return refuse(note)

    # Amount threshold that requires approval or a block.
    match = re.search(
        r"(?:supere[n]?|superior(?:es)?\s+a|mayor(?:es)?\s+(?:que|a)|mas\s+de|above|>)\s*"
        r"(?:los\s+)?([\d][\d.,\s]*)\s*(?:euros|eur|€)?", text)
    if match:
        threshold = match.group(1).strip()
        hard = any(word in text for word in _HARD_BLOCK)
        on_fail = "NO_PAGAR" if hard else "ESCALAR"
        if hard or any(word in text for word in
                       ("aprobacion", "autoriz", "responsable", "firma", "escalar", "revision")):
            return draft([{"field": "invoice.total", "op": "<=", "value": threshold}],
                         on_fail=on_fail)

    # Payment terms expressed as a maximum number of days.
    match = re.search(r"(\d{1,3})\s*dias", text)
    if match and any(word in text for word in ("plazo", "vencimiento", "pago", "condiciones")):
        return draft([{"field": "supplier.payment_terms_days", "op": "<=",
                       "value": int(match.group(1))}])

    # The ERP owns the paid/pending state.
    if "pendiente" in text and any(word in text for word in ("erp", "pedido", "asiento")):
        hard = any(word in text for word in _HARD_BLOCK + ("dos veces", "duplicad"))
        return draft([{"field": "erp.order_payment_status", "op": "==", "value": "PENDIENTE"}],
                     on_fail="NO_PAGAR" if hard else "ESCALAR")

    # Bank account must match the master.
    if "iban" in text and any(word in text for word in ("coincid", "maestro", "mismo", "igual")):
        return draft([{"field": "invoice.iban", "op": "==",
                       "value": {"field": "supplier.iban"}}])

    # Tax id must match the master.
    if "nif" in text and any(word in text for word in ("coincid", "maestro", "mismo", "igual")):
        return draft([{"field": "invoice.supplier_tax_id", "op": "==",
                       "value": {"field": "supplier.tax_id"}}])

    # Only a given currency may be paid.
    if "euro" in text and any(word in text for word in ("solo", "unicamente", "exclusivamente")):
        return draft([{"field": "invoice.currency", "op": "==", "value": "EUR"}])

    # The supplier must be active in the master.
    if "activo" in text and "proveedor" in text:
        return draft([{"field": "supplier.active", "op": "==", "value": True}])

    # Required fields on the document.
    required = [(("numero de pedido", "pedido"), "invoice.order_reference"),
                (("nif",), "invoice.supplier_tax_id"),
                (("iban",), "invoice.iban"),
                (("fecha",), "invoice.issue_date")]
    if any(word in text for word in ("debe indicar", "debe llevar", "debe incluir",
                                     "es obligatorio", "obligatoriamente")):
        clauses = [{"field": field, "op": "exists"}
                   for probes, field in required if any(p in text for p in probes)]
        if clauses:
            return draft(clauses)

    return None


def generate_structured(rule_text: str) -> dict | None:
    """Ask DeepSeek for a condition draft; None when unavailable/unparseable."""
    raw = _deepseek_chat(_structured_prompt(rule_text), max_tokens=400)
    if raw is None:
        return None
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group() if match else raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    parsed["compiled_by"] = "deepseek"
    return parsed


def compile_new_rule(rule_text: str, use_llm: bool = True,
                     gen_python: bool = True) -> dict:
    """Compile a NEW/demoted rule text into an executable condition.

    Heuristic first (deterministic), then the model. Whatever comes back is
    compiled and validated against the field registry; anything that does not
    validate becomes `pending` carrying the reason, never a condition the
    evaluator would reject later.
    """
    reasons = []
    condition = None
    compiled_by = None
    for draft in (heuristic_structured(rule_text),
                  generate_structured(rule_text) if use_llm else None):
        if draft is None:
            continue
        candidate, reason = compile_draft(draft)
        if candidate is not None:
            condition = candidate
            compiled_by = draft.get("compiled_by")
            break
        reasons.append(f"{draft.get('compiled_by') or 'draft'}: {reason}")

    on_fail = "ESCALAR"
    if condition is not None:
        on_fail = "NO_PAGAR" if condition["else"] == "FAIL" else "ESCALAR"
    else:
        condition = {"kind": "pending",
                     "note": "; ".join(reasons) or "no draft could be compiled",
                     "compiled_by": None}

    # Python generation stays available for human review, but the evaluator
    # only runs a python_check for a canonical rule, so it never makes a NEW
    # rule runnable. Reporting otherwise is how these rules looked executable.
    logic = generate_check(rule_text) if gen_python and use_llm else None
    if logic and logic.get("valid"):
        condition = {**condition, "python": {
            "function": logic.get("function", "check"),
            "source": logic.get("source"),
            "valid": True,
            "generated_by": logic.get("generated_by"),
            "smoke": logic.get("smoke"),
        }}

    return {
        "condition": condition,
        "logic": logic,
        "runnable": condition.get("kind") == "structured",
        "compiled_by": compiled_by,
        "params": {},
        "on_fail": on_fail,
    }


def enrich_new_rules(merged: dict, use_llm: bool = True,
                     gen_python: bool = False) -> dict:
    """Give every non-canonical rule an executable condition, or a reason.

    Every condition is validated against the field registry here -- including
    one compiled earlier during discovery -- so a rule that the evaluator would
    refuse is visible in the ruleset at build time. Mutates and returns
    `merged`. Deterministic when use_llm=False.
    """
    compiled = 0
    unexecutable = []
    # A policy that accepts sheet-authored rules still only gets the ones that
    # validate, and never a rule demoted for weak classifier grounding.
    enable_new = bool((merged.get("metrics") or {}).get("enable_new_rules_default", False))
    for rule in merged.get("rules") or []:
        if rule.get("origin") == "canonical":
            continue
        condition = rule.get("condition") or {}
        reason = condition_authoring.validate(condition) \
            if condition.get("kind") == "structured" else "NOT_STRUCTURED"
        if reason is not None:
            result = compile_new_rule(rule.get("text") or "", use_llm=use_llm,
                                      gen_python=gen_python and use_llm)
            rule["condition"] = result["condition"]
            rule["on_fail"] = result["on_fail"] or rule.get("on_fail") or "ESCALAR"
            condition = rule["condition"]
            reason = condition_authoring.validate(condition) \
                if condition.get("kind") == "structured" else condition.get("note")

        runnable = condition.get("kind") == "structured" and reason is None
        # `runnable` belongs to the rule: a condition carrying an extra key is
        # rejected by the executor as UNKNOWN_CONDITION_PROPERTY.
        rule["runnable"] = runnable
        if runnable:
            # A structured condition inlines its literals; params on top of one
            # are refused by the planner as UNSUPPORTED_STRUCTURED_PARAMETERS.
            rule["params"] = {}
            compiled += 1
            if enable_new and not rule.get("demoted"):
                rule["enabled"] = True
        else:
            rule["compile_error"] = reason or "not compiled"
            unexecutable.append({"id": rule.get("id"), "reason": rule["compile_error"],
                                 "text": rule.get("text")})

    stats = merged.setdefault("stats", {})
    stats["new_compiled"] = compiled
    stats["new_unexecutable"] = len(unexecutable)
    stats["unexecutable_rules"] = unexecutable
    stats["enabled"] = sum(1 for rule in merged.get("rules") or []
                           if rule.get("enabled"))
    return merged


# --------------------------------------------------------------------------- #
# Public API (python check generation)
# --------------------------------------------------------------------------- #
def generate_check(rule_text: str) -> dict:
    """Generate + validate a Python `check` for a rule. Returns a logic dict.

    On success: {language, function, source, generated_by, valid: True, smoke}.
    On any failure: {..., valid: False, error} (never presented as runnable).
    """
    raw = _deepseek_chat(_prompt(rule_text))
    if raw is None:
        return {"language": "python", "status": "no_code", "valid": False,
                "error": "DeepSeek unavailable", "generated_by": None}
    source = _extract_code(raw)
    ok, why = validate_source(source)
    if not ok:
        return {"language": "python", "status": "invalid", "valid": False,
                "error": why, "source": source, "generated_by": "deepseek"}
    ran, detail = smoke_run(source)
    model = helmcode.authoring_model()
    return {
        "language": "python",
        "function": "check",
        "source": source + ("\n" if not source.endswith("\n") else ""),
        "generated_by": f"deepseek:{model}",
        "valid": True,
        "smoke_ok": ran,
        "smoke": detail,
    }
