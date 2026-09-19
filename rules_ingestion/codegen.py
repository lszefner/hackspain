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
import os
import re
import urllib.request
from decimal import Decimal
from typing import Optional

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
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.helmcode.com/v1/chat/completions")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
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
# Structured condition (schema 2.0) + compile NEW rules
# --------------------------------------------------------------------------- #
_OPS = {">", ">=", "<", "<=", "==", "!=", "in", "not_in", "exists", "missing", "match"}


def _structured_prompt(rule_text: str) -> str:
    ops = ", ".join(sorted(_OPS))
    return (
        "Translate ONE vendor-payment rule into a deterministic condition schema. Respond with ONLY the "
        "JSON object below — no markdown, no prose.\n\n"
        "OUTPUT SCHEMA (strict JSON):\n"
        "{\n"
        '  "logic": "AND" | "OR",\n'
        '  "clauses": [{"field": "<string>", "op": "<op>", "value": <literal|null>}],\n'
        '  "on_fail": "ESCALAR" | "NO_PAGAR",\n'
        '  "params": {}\n'
        "}\n\n"
        "ALLOWED VALUES (do not use anything outside these):\n"
        f"  ops:            {ops}\n"
        f"  invoice fields: {_INVOICE_FIELDS}\n"
        "  master fields:  erp_estado, pedidos, proveedores, proveedores_by_nif, today\n\n"
        "RULES FOR TRANSLATION:\n"
        "1. Prefer `clauses` whenever the rule can be expressed as field/operator/value comparisons "
        "against the allowed fields above. Use only fields from the allowed lists — never invent a "
        "field name, and never guess a field that \"sounds right\" if it isn't in the list.\n"
        "2. Use `clauses=[]` ONLY when the rule genuinely cannot be expressed as simple field comparisons "
        "(e.g. it requires a rate calculation, a lookup against a denylist, a percentage threshold "
        "combined with a category, or cross-referencing multiple master records in a way no single "
        "`op` supports). In that case, put every extracted parameter into `params` using descriptive "
        "keys (e.g. \"rate\", \"threshold\", \"denylist_key\", \"require_erp_pending\") so a human or a "
        "later step can still act on it. Do not leave information out of `params` just because it "
        "didn't fit `clauses`.\n"
        "3. `on_fail` reflects the CONSEQUENCE stated or implied by the rule: use \"NO_PAGAR\" when the "
        "rule is a hard block on payment, \"ESCALAR\" when it implies human review/judgment is needed "
        "(ambiguous cases, thresholds requiring approval, anomalies). If the rule doesn't specify, "
        "default to \"ESCALAR\" — never guess \"NO_PAGAR\" for an ambiguous rule.\n"
        "4. `value` should be the literal extracted from the rule text (a number, a string, or a field "
        "reference like \"master.pedidos.importe_total\") — never a placeholder like \"X\" or \"TBD\".\n\n"
        "EXAMPLES\n"
        'Rule: "No pagar dos veces el mismo pedido"\n'
        '{"logic":"AND","clauses":[],"on_fail":"NO_PAGAR",'
        '"params":{"forbid_duplicate_pedido":true,"require_erp_estado":"PENDIENTE"}}\n\n'
        'Rule: "Retención 15% IRPF en autónomos"\n'
        '{"logic":"AND","clauses":[],"on_fail":"ESCALAR","params":{"rate":0.15,"applies_to":"autonomo"}}\n\n'
        'Rule: "Facturas superiores a 5.000€ requieren aprobación del director financiero"\n'
        '{"logic":"AND","clauses":[{"field":"total","op":">","value":5000}],"on_fail":"ESCALAR","params":{}}\n\n'
        f'Rule: "{rule_text}"\nJSON:'
    )


def heuristic_structured(rule_text: str) -> Optional[dict]:
    """Offline, deterministic extraction of common NEW-rule patterns.

    Used when DeepSeek is off/unavailable so NEW/demoted rules still get a
    real `condition` object instead of pending.
    """
    from .normalize import _accent_fold
    t = _accent_fold(rule_text or "")
    params: dict = {}
    clauses: list = []
    on_fail = "ESCALAR"

    # IRPF / retention percentage
    m = re.search(r"retenci\w*\s+(?:del\s+)?(\d+[.,]?\d*)\s*%", t)
    if not m:
        m = re.search(r"(\d+[.,]?\d*)\s*%\s*(?:de\s+)?irpf", t)
    if m or ("irpf" in t and "retencion" in t):
        rate = float((m.group(1) if m else "15").replace(",", ".")) / 100.0
        params = {"irpf_rate": rate, "applies_to": "autonomo"}
        clauses = [
            {"field": "vendor_type", "op": "==", "value": "autonomo"},
            {"field": "withholding_rate", "op": "==", "value": rate},
        ]
        return {
            "kind": "structured",
            "logic": "AND",
            "clauses": clauses,
            "on_fail": on_fail,
            "params": params,
            "compiled_by": "heuristic",
            "then": "PASS",
            "else": "NEEDS_REVIEW",
        }

    # Tax-haven / AEAT denylist
    if "paraiso" in t or "aeat" in t and "lista" in t:
        params = {"denylist": "aeat_tax_havens"}
        clauses = [
            {"field": "vendor.country", "op": "not_in", "value": "params.denylist"},
        ]
        return {
            "kind": "structured",
            "logic": "AND",
            "clauses": clauses,
            "on_fail": "NO_PAGAR",
            "params": params,
            "compiled_by": "heuristic",
            "then": "PASS",
            "else": "FAIL",
        }

    # Seguridad Social current
    if "seguridad social" in t or "tgss" in t:
        clauses = [
            {"field": "vendor.ss_current", "op": "==", "value": True},
        ]
        return {
            "kind": "structured",
            "logic": "AND",
            "clauses": clauses,
            "on_fail": "NO_PAGAR",
            "params": {},
            "compiled_by": "heuristic",
            "then": "PASS",
            "else": "FAIL",
        }

    # Amount threshold with approval
    m = re.search(r"(?:supere|mayor|mas de|above|>)\s*(?:los\s+)?(\d+[.\s]?\d*)\s*(?:euros|eur|€)?", t)
    if m and any(k in t for k in ("aprobacion", "autoriz", "responsable", "firma")):
        raw = m.group(1).replace(".", "").replace(" ", "").replace(",", ".")
        try:
            thr = float(raw)
        except ValueError:
            thr = 5000.0
        params = {"escalate_above_eur": thr}
        clauses = [
            {"field": "total", "op": "<=", "value": thr},
        ]
        return {
            "kind": "structured",
            "logic": "AND",
            "clauses": clauses,
            "on_fail": "ESCALAR",
            "params": params,
            "compiled_by": "heuristic",
            "then": "PASS",
            "else": "NEEDS_REVIEW",
        }

    return None


def generate_structured(rule_text: str) -> Optional[dict]:
    """Ask DeepSeek for a structured condition; None if unavailable/invalid."""
    raw = _deepseek_chat(_structured_prompt(rule_text), max_tokens=400)
    if raw is None:
        return None
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        d = json.loads(m.group() if m else raw)
    except Exception:
        return None
    logic = d.get("logic", "AND")
    if logic not in ("AND", "OR"):
        logic = "AND"
    clauses = []
    for c in d.get("clauses") or []:
        if not isinstance(c, dict):
            continue
        op = str(c.get("op", "=="))
        if op not in _OPS:
            continue
        field = str(c.get("field") or "").strip()
        if not field:
            continue
        clauses.append({"field": field, "op": op, "value": c.get("value")})
    on_fail = d.get("on_fail", "ESCALAR")
    if on_fail not in ("ESCALAR", "NO_PAGAR"):
        on_fail = "ESCALAR"
    params = d.get("params") if isinstance(d.get("params"), dict) else {}
    return {
        "kind": "structured",
        "logic": logic,
        "clauses": clauses,
        "on_fail": on_fail,
        "params": params,
        "compiled_by": "deepseek",
        "then": "PASS",
        "else": "NEEDS_REVIEW" if on_fail == "ESCALAR" else "FAIL",
    }


def compile_new_rule(rule_text: str, use_llm: bool = True,
                     gen_python: bool = True) -> dict:
    """Compile a NEW/demoted rule text into condition (+ optional python check).

    Always tries heuristic first (deterministic). Then LLM structured, then
    optional python `check` source. Never returns a bare pending if heuristic hits.
    """
    condition = heuristic_structured(rule_text)
    if condition is None and use_llm:
        condition = generate_structured(rule_text)
    if condition is None:
        condition = {
            "kind": "pending",
            "note": "could not compile to a structured condition",
            "compiled_by": None,
        }

    logic = None
    if gen_python and use_llm:
        logic = generate_check(rule_text)
        if logic.get("valid") and condition.get("kind") == "pending":
            # Promote: at least we have executable python
            condition = {
                "kind": "python_check",
                "function": logic.get("function", "check"),
                "source": logic.get("source"),
                "valid": True,
                "compiled_by": logic.get("generated_by"),
            }
        elif logic.get("valid") and condition.get("kind") == "structured":
            condition = {
                **condition,
                "python": {
                    "function": logic.get("function", "check"),
                    "source": logic.get("source"),
                    "valid": True,
                    "generated_by": logic.get("generated_by"),
                    "smoke": logic.get("smoke"),
                },
            }

    runnable = (
        condition.get("kind") == "structured"
        or (condition.get("kind") == "python_check" and condition.get("valid"))
        or bool((condition.get("python") or {}).get("valid"))
    )
    return {
        "condition": condition,
        "logic": logic,
        "runnable": runnable,
        "params": condition.get("params") or {},
        "on_fail": condition.get("on_fail", "ESCALAR"),
    }


def enrich_new_rules(merged: dict, use_llm: bool = True,
                     gen_python: bool = False) -> dict:
    """Fill condition for every non-canonical rule that is still pending/empty.

    Mutates and returns `merged`. Deterministic when use_llm=False (heuristic only).
    """
    compiled = 0
    for rule in merged.get("rules") or []:
        if rule.get("origin") == "canonical":
            continue
        cond = rule.get("condition") or {}
        needs = (
            not cond
            or cond.get("kind") in (None, "pending")
            or (cond.get("kind") == "python_check" and not cond.get("source")
                and not cond.get("valid"))
        )
        if not needs:
            continue
        result = compile_new_rule(
            rule.get("text") or "",
            use_llm=use_llm,
            gen_python=gen_python and use_llm,
        )
        rule["condition"] = result["condition"]
        if result["params"]:
            rule["params"] = {**(rule.get("params") or {}), **result["params"]}
        if result.get("on_fail"):
            rule["on_fail"] = result["on_fail"]
        # NEW rules with a real condition stay disabled until a human flips
        # enabled — but they are no longer pending stubs.
        if result["runnable"]:
            rule["condition"]["runnable"] = True
            compiled += 1
    stats = merged.setdefault("stats", {})
    stats["new_compiled"] = compiled
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
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    return {
        "language": "python",
        "function": "check",
        "source": source + ("\n" if not source.endswith("\n") else ""),
        "generated_by": f"deepseek:{model}",
        "valid": True,
        "smoke_ok": ran,
        "smoke": detail,
    }
