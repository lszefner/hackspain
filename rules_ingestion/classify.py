"""classify.py · rule-text classification, JEV-first with graceful degradation.

Three tiers, in order of preference:

  1. JEV (TypeSafe System One)  -- PRIMARY. Noul(is_rule) + Choice(maps_to) in a
     single /v1/systemone call. Calibrated confidence, no hallucination.
  2. LLM (Anthropic Claude)     -- FALLBACK. Only for lines JEV can't resolve.
  3. Lexical classifier         -- OFFLINE DEGRADE. If the JEV API is unreachable
     the pipeline still runs (deterministic, dependency-free) so authoring is
     never blocked by a provider outage.

All of this is AUTHORING time. The classifier's output is frozen into
rules.store.json, so a payment DECISION stays 100% deterministic regardless of
which tier produced a rule (the tier + confidence are recorded in the trace).
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .catalog import CATALOG, CHOICE_LABELS, all_lexicons, catalog_brief, lexicon_grounded
from .normalize import _accent_fold

# --- live debug (set by build_rules --verbose) ---
DEBUG = False


def set_debug(on: bool) -> None:
    global DEBUG
    DEBUG = on


def _dbg(msg: str) -> None:
    if DEBUG:
        print(msg, flush=True)


# --- routing thresholds ---
# Rubric-calibrated bands (lexical Noul + JEV noul):
#   >= IS_RULE_HI  -> treat as a payment rule (ACTIVATE / NEW_RULE)
#   IS_RULE_LO..HI -> uncertain -> LLM_FALLBACK
#   <  IS_RULE_LO  -> NOT_A_RULE
IS_RULE_HI = 0.60
IS_RULE_LO = 0.40
# Product rule: a canonical mapping must be >= 0.8 confident to activate WITHOUT
# a human/LLM in the loop; below that we route to the LLM (DeepSeek).
ACTIVATE_CONF = 0.80
# The offline lexical tier is a weaker, best-effort signal used only when the
# JEV API is unreachable; it clears at a lower bar and otherwise flags for review.
ACTIVATE_CONF_LEXICAL = 0.60


# --------------------------------------------------------------------------- #
# .env loading (no dependency): populate os.environ from a gitignored .env
# --------------------------------------------------------------------------- #
def _load_dotenv() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, ".env"),
                 os.path.join(os.path.dirname(here), ".env")):
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())



# --------------------------------------------------------------------------- #
# Shared result type + router
# --------------------------------------------------------------------------- #
@dataclass
class ClassifyResult:
    text: str
    is_rule: float
    maps_to: str
    maps_to_conf: float
    route: str                       # ACTIVATE | NEW_RULE | NOT_A_RULE | LLM_FALLBACK
    method: str                      # jev | llm | lexical | jev_lexical_degraded ...
    scores: Dict[str, float] = field(default_factory=dict)
    reason: str = ""
    usage: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "text": self.text, "is_rule": self.is_rule, "maps_to": self.maps_to,
            "maps_to_conf": self.maps_to_conf, "route": self.route,
            "method": self.method, "scores": self.scores, "reason": self.reason,
            "usage": self.usage,
        }


def route_for(is_rule: float, label: str, conf: float, tier: str,
              activate_at: float = ACTIVATE_CONF) -> Tuple[str, str]:
    if is_rule >= IS_RULE_HI:
        if conf >= activate_at:
            if label == "NONE":
                return "NEW_RULE", f"[{tier}] confidently no canonical match (conf {conf:.2f}) -> candidate new rule"
            return "ACTIVATE", f"[{tier}] is_rule {is_rule:.2f}, maps_to {label} @ {conf:.2f}"
        # rule-like but below the confidence gate -> send to the LLM
        return "LLM_FALLBACK", f"[{tier}] maps_to {label} @ {conf:.2f} < {activate_at} -> LLM"
    if is_rule >= IS_RULE_LO:
        return "LLM_FALLBACK", f"[{tier}] uncertain whether this is a rule (is_rule {is_rule:.2f})"
    return "NOT_A_RULE", f"[{tier}] is_rule {is_rule:.2f} < {IS_RULE_LO}"


def route_declared(label: str, conf: float, tier: str,
                   activate_at: float = ACTIVATE_CONF) -> Tuple[str, str]:
    """Routing for a DECLARED rules source (e.g. Norma_Pagos_v*): every line is a
    rule by definition, so we ignore Noul and route on Choice confidence only."""
    if conf >= activate_at:
        if label == "NONE":
            return "NEW_RULE", f"[{tier}] declared rule with no canonical match (conf {conf:.2f}) -> new rule"
        return "ACTIVATE", f"[{tier}] declared rule -> {label} @ {conf:.2f}"
    return "LLM_FALLBACK", f"[{tier}] declared rule, maps_to {label} @ {conf:.2f} < {activate_at} -> LLM"


# --------------------------------------------------------------------------- #
# JEV question builders (from the canonical catalog)
# --------------------------------------------------------------------------- #
# Richer, discriminative descriptions for JEV Choice (better than bare titles).
# Example phrases stay in Spanish: they mirror Alberto's Spanish source docs.
_CHOICE_CRITERIA = {
    "VENDOR": "The vendor/NIF must exist in the master and the invoice IBAN must match the master's.",
    "DUPLICATES": "Do not pay twice: same invoice/vendor, same amount+date, or order already PAGADA / state not PENDIENTE.",
    "AMOUNT": "Amounts, VAT and total (base+VAT) correct, within tolerance, and matching the purchase-order amount.",
    "AUTHORIZATION": "Requires human approval when the amount exceeds a set threshold.",
    "DATES": "The invoice date must be valid, not in the future, and within the vendor's payment terms.",
    "MISSING": "Required fields are missing (NIF/IBAN/pedido/importe/IVA/fecha), or there is an anomaly/doubt that must be escalated to a human.",
    "NONE": "Does not match any prior canonical rule (possible new rule).",
}


def noul_question() -> dict:
    # Discovery gate (declared sources bypass Noul). Additive rubric is encoded
    # in instructions; JEV returns a single calibrated float — not custom JSON.
    # Spanish examples stay: they mirror Alberto's Spanish source documents.
    return {
        "type": "noul",
        "instructions": (
            "Score whether THIS line is an accounts-payable / vendor-invoice RULE "
            "(validation, payment, withholding, matching, authorization, or rejection). "
            "Compute mechanically from signals — do not holistically guess.\n"
            "Additive rubric (internal; your Noul score should reflect it):\n"
            "  DEONTIC (up to ~half of belief):\n"
            "    strong: debe / no se puede / solo si / en caso de / se rechaza / requiere / "
            "obligatorio / prohibido / if-then / threshold (\"si el importe supera…\")\n"
            "    weak: standing policy without modal (\"retención 15% IRPF en autónomos\")\n"
            "  DOMAIN (up to ~half):\n"
            "    strong: IBAN, NIF/CIF, IVA, IRPF/retención, importe, duplicado de pago, "
            "fecha/vencimiento, pedido/PO, ERP, campo obligatorio, autorización/"
            "denegación de pago, proveedor/maestro matching\n"
            "    weak: adjacent finance/admin that only loosely relates to paying an invoice\n"
            "  NOISE (push toward false even if a finance word appears):\n"
            "    schedules, meetings, office logistics, phone numbers, logos, greetings, "
            "personal notes, errands (\"comprar café\"), parking/facilities; "
            "\"reunión para hablar de facturas\" = meeting, NOT a rule.\n"
            "Anchors (sanity-check only):\n"
            "  TRUE high  — \"IBAN debe coincidir con el maestro\"\n"
            "  TRUE high  — \"no pagar dos veces el mismo pedido\"\n"
            "  TRUE mid   — \"retención 15% IRPF en autónomos\" (implicit policy + tax term)\n"
            "  FALSE      — \"parking cierra a las 22h\" / \"comprar café\" / \"reunión el martes\""
        ),
        "criteria": {
            "true": (
                "The line states a norm/condition that gates processing or paying a vendor "
                "invoice: vendor/NIF/IBAN checks, amounts/VAT/withholding, dates, duplicates, "
                "ERP status, required fields, payment authorization, or payment denial."
            ),
            "false": (
                "Not a payment rule: workplace noise (hours, meetings, logistics, errands, "
                "phones, logos, greetings, personal notes) or narrative without normative "
                "force over invoice payment — even if the word \"factura\" appears incidentally."
            ),
        },
    }


def choice_question() -> dict:
    return {
        "type": "choice",
        "instructions": "Which canonical vendor-payment rule does this line map to?",
        "criteria": dict(_CHOICE_CRITERIA),
    }


# --------------------------------------------------------------------------- #
# TIER 1 · JEV (TypeSafe System One)
# --------------------------------------------------------------------------- #
class JEVClient:
    """TypeSafe System One client. Noul + Choice in one /v1/systemone call."""

    def __init__(self, api_key: Optional[str] = None, model: str = "jev-latest",
                 base_url: str = "https://api.typesafe.ai/v1/systemone",
                 timeout: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
        self.model = model
        self.base_url = base_url
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def classify(self, text: str) -> dict:
        """Call JEV. Returns dict or raises (caller degrades on exception)."""
        body = {
            "model": self.model,
            "state": text,
            "questions": {"is_rule": noul_question(), "maps_to": choice_question()},
        }
        req = urllib.request.Request(
            self.base_url, data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
        )
        _dbg(f"    → JEV  POST {self.base_url}  state=“{text[:60]}…”")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ans = data.get("answers", {})
        if DEBUG:
            ch = ans.get("maps_to", {})
            _dbg(f"    ← JEV  is_rule={ans.get('is_rule',{}).get('noul')} "
                 f"choice={ch.get('choice')} conf={ch.get('confidence')} "
                 f"probs={ch.get('probabilities')} usage={data.get('usage')}")
        is_rule = float(ans.get("is_rule", {}).get("noul", 0.0))
        choice = ans.get("maps_to", {})
        label = str(choice.get("choice", "NONE")).upper()
        if label not in CHOICE_LABELS:
            label = "NONE"
        return {
            "is_rule": round(is_rule, 4),
            "maps_to": label,
            "maps_to_conf": round(float(choice.get("confidence", 0.0)), 4),
            "probabilities": choice.get("probabilities", {}),
            "usage": data.get("usage"),
            "model": data.get("model"),
        }


# --------------------------------------------------------------------------- #
# TIER 3 · Lexical classifier (offline degrade)  — Noul + Choice, deterministic
# --------------------------------------------------------------------------- #
# Rubric checklist (not a single opaque score). Each hit adds/subtracts logit
# weight; sigmoid maps to [0,1]. Calibrated so real payment rules clear
# IS_RULE_HI (~0.60+) and workplace noise stays < IS_RULE_LO (~0.40).
_DEONTIC = {
    "debe", "deben", "debera", "deberan", "pagar", "pago", "pagarse", "pagado",
    "escalar", "nunca", "solo", "requiere", "requieren", "requerir",
    "coincida", "coincide", "coincidir", "prohibido", "prohibida", "prohibir",
    "obligatorio", "obligatoria", "valida", "valido", "verificar", "comprobar",
    "aplicar", "aplicara", "admiten", "admitir", "rechazar", "rechazarse",
    "tramitar", "detener", "existir", "pertenecer", "confirmar", "exigir",
}
_DOMAIN = {
    "nif", "iban", "pedido", "importe", "iva", "fecha", "proveedor", "proveedores",
    "estado", "maestro", "total", "base", "factura", "facturas", "moneda",
    "tolerancia", "pendiente", "pagada", "irpf", "retencion", "retenciones",
    "autonomo", "autonomos", "aeat", "paraiso", "paraisos", "aprobacion",
    "autorizacion", "umbral", "erp", "duplicado", "duplicada", "ss", "tgss",
    "imponible", "withholding",
}
_NOTE_NEG = {
    "acordarse", "preguntar", "roto", "llaman", "cafe", "planta", "sonia",
    "viernes", "mirar", "hueco", "aplica", "recordar", "avisar", "ojo",
    "parking", "telefono", "centralita", "reunion", "logo", "bombilla",
    "pasillo", "finde", "hola", "gracias", "asunto", "catalogo", "word",
    "plantillas", "xxxxxx", "xxxxx",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LEADING_NUM_RE = re.compile(r"^\s*\d+\s*[.)\-]")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def tokenize(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(_accent_fold(text)) if len(t) >= 2]


def _stem_hit(toks: set, vocab: set) -> set:
    """Match exact tokens OR shared stem (factura/facturas, pagar/pagarse)."""
    hits = set(toks & vocab)
    for t in toks:
        for v in vocab:
            if t == v:
                continue
            # cheap Spanish plural / conjugation overlap (len>=4)
            if len(v) >= 4 and len(t) >= 4 and (t.startswith(v) or v.startswith(t)):
                hits.add(v)
    return hits


class Noul:
    """Lexical binary proposition: P(text is a payment rule).

    Rubric (logit sum → sigmoid):
      + numbered norm line          (+2.0)
      + deontic / imperative verb   (+1.4 if any)
      + payment-domain tokens       (+1.0 + 0.45 per extra, capped)
      + long enough to be a clause  (+0.3 if >= 8 tokens)
      - workplace-noise tokens      (-2.2 if any)
      - short + no domain           (-1.0)
      - domain-only without deontic (-0.8)  # "facturas en el armario"
    """

    @staticmethod
    def rubric(text: str) -> Dict[str, float]:
        """Return named rubric contributions (for debug / tests)."""
        toks = set(tokenize(text))
        words = tokenize(text)
        deontic = _stem_hit(toks, _DEONTIC)
        domain = _stem_hit(toks, _DOMAIN)
        noise = _stem_hit(toks, _NOTE_NEG)
        parts: Dict[str, float] = {
            "base": -1.2,
            "numbered": 0.0,
            "deontic": 0.0,
            "domain": 0.0,
            "length": 0.0,
            "noise": 0.0,
            "short": 0.0,
            "domain_only": 0.0,
        }
        if _LEADING_NUM_RE.match(text or ""):
            parts["numbered"] = 2.0
        if deontic:
            parts["deontic"] = 1.4
        if domain:
            parts["domain"] = 1.0 + 0.45 * min(len(domain) - 1, 3)
        if len(words) >= 8:
            parts["length"] = 0.3
        if noise:
            parts["noise"] = -2.2
        if len(words) < 5 and not domain:
            parts["short"] = -1.0
        # archival / logistics mentioning facturas but no obligation to pay
        if domain and not deontic and not _LEADING_NUM_RE.match(text or ""):
            parts["domain_only"] = -0.8
        return parts

    @staticmethod
    def is_rule(text: str) -> float:
        parts = Noul.rubric(text)
        score = sum(parts.values())
        return round(_sigmoid(score), 4)


# When two lexicons both fire, prefer the more specific domain over a generic
# catch-all. Order is highest-precedence first (named object / obligation >
# threshold framing > arithmetic > catch-all MISSING).
_MAPS_TO_PRECEDENCE: Tuple[str, ...] = (
    "VENDOR", "DUPLICATES", "AUTHORIZATION", "DATES", "AMOUNT", "MISSING",
)
# VENDOR may only override MISSING when match/fraud evidence is present —
# listing NIF/IBAN as *missing required fields* must stay MISSING.
_VENDOR_OVER_MISSING: frozenset = frozenset({
    "coincide", "coincida", "maestro", "fraude", "titular", "banco", "activo",
})


class Choice:
    """Lexical router to one canonical rule (or NONE) with calibrated confidence."""

    def __init__(self, lexicons: Optional[Dict[str, Tuple[str, ...]]] = None):
        self.lexicons = lexicons or all_lexicons()
        df: Dict[str, int] = {}
        for toks in self.lexicons.values():
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        self.weight = {t: 1.0 / n for t, n in df.items()}

    def maps_to(self, text: str) -> Tuple[str, float, Dict[str, float]]:
        toks = set(tokenize(text))
        scores: Dict[str, float] = {}
        for key, lex in self.lexicons.items():
            s = sum(self.weight[t] for t in (toks & set(lex)))
            if s:
                scores[key] = round(s, 4)
        if not scores:
            return "NONE", 0.0, {}

        # Raw winner by score …
        raw = max(scores, key=scores.get)
        # … then precedence overrides for known multi-category overlaps:
        # AUTHORIZATION beats AMOUNT on threshold+approval lines; VENDOR beats
        # MISSING only when IBAN/NIF *match* evidence is present (not field lists).
        label = raw
        if "AUTHORIZATION" in scores and "AMOUNT" in scores:
            label = "AUTHORIZATION"
        elif ("VENDOR" in scores and "MISSING" in scores
              and (toks & _VENDOR_OVER_MISSING)):
            label = "VENDOR"
        elif len(scores) > 1:
            # Near-ties (within 10%): prefer higher-precedence label.
            top = scores[raw]
            contenders = [k for k, v in scores.items() if v >= top * 0.9]
            if len(contenders) > 1:
                rank = {k: i for i, k in enumerate(_MAPS_TO_PRECEDENCE)}
                label = min(contenders, key=lambda k: rank.get(k, 99))

        top1 = scores[label]
        rivals = [v for k, v in scores.items() if k != label]
        top2 = max(rivals) if rivals else 0.0
        conf = round(top1 / (top1 + top2), 4) if (top1 + top2) else 0.0
        return label, conf, scores


class LexicalJEV:
    """Noul + Choice as one offline unit (mirrors the JEV interface)."""

    def __init__(self) -> None:
        self.noul = Noul()
        self.choice = Choice()

    def classify(self, text: str) -> ClassifyResult:
        is_rule = self.noul.is_rule(text)
        label, conf, scores = self.choice.maps_to(text)
        route, reason = route_for(is_rule, label, conf, "lexical",
                                  activate_at=ACTIVATE_CONF_LEXICAL)
        return ClassifyResult(text, is_rule, label, conf, route, "lexical", scores, reason)


# --------------------------------------------------------------------------- #
# TIER 2 · LLM fallback (DeepSeek, OpenAI-compatible, stdlib HTTP, graceful)
# --------------------------------------------------------------------------- #
class DeepSeekFallback:
    """DeepSeek chat fallback (via the HelmCode gateway) for lines JEV can't clear
    the 0.8 gate on.

    Uses the OpenAI-compatible /chat/completions endpoint over stdlib urllib
    (no SDK dependency). Graceful: no key / provider down -> returns None and
    the caller flags the line for human review. Base URL and model are override-
    able via env (DEEPSEEK_BASE_URL / DEEPSEEK_MODEL) without code changes.
    """

    def __init__(self, model: Optional[str] = None,
                 base_url: Optional[str] = None,
                 timeout: float = 30.0) -> None:
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.base_url = base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.helmcode.com/v1/chat/completions")
        self.timeout = timeout
        self.api_key = os.environ.get("DEEPSEEK_API_KEY")

    def available(self) -> bool:
        return bool(self.api_key)

    def classify(self, text: str) -> Optional[dict]:
        if not self.available():
            return None
        labels = ", ".join(CHOICE_LABELS)
        prompt = (
            "You are a fallback classifier for vendor-payment rules. You are called only when a primary "
            "extraction pipeline could not confidently classify a line. Respond with ONLY the JSON object "
            "below — no markdown, no prose, no explanation outside the JSON.\n\n"
            "STRICT GROUNDING (read before scoring):\n"
            "- You may use ONLY the canonical labels listed below. Never invent a label, never rename one, "
            "never combine two labels.\n"
            "- A label match requires SEMANTIC alignment with that label's description below — not just a "
            "shared keyword. Mentioning \"importe\" does not make something AMOUNT; mentioning \"proveedor\" "
            "does not make something VENDOR. If the line's actual mechanism doesn't match the description, "
            "do not force it.\n"
            "- If the line IS a payment rule but its mechanism doesn't match any canonical description below, "
            "you MUST return maps_to=NONE. This is the expected, correct output for a genuinely new rule — "
            "do not stretch a canonical label to avoid returning NONE.\n"
            "- `evidence` must be a LITERAL, VERBATIM substring copied from the input line — not a paraphrase, "
            "not a summary. If maps_to=NONE, evidence must be \"\".\n\n"
            "CANONICAL RULES (the only labels you may use — labels are: %s):\n%s\n\n"
            "OUTPUT SCHEMA (strict JSON):\n"
            "{\n"
            '  "is_rule": <float 0.0-1.0>,\n'
            '  "maps_to": "<one of the canonical labels above, or NONE>",\n'
            '  "confidence": <float 0.0-1.0>,\n'
            '  "evidence": "<verbatim substring of the line, or \\"\\" if maps_to=NONE>"\n'
            "}\n\n"
            "SELF-CHECK before answering (do this silently, output only the final JSON):\n"
            "1. Does the line express an obligation/condition/consequence about invoices or vendor payment? "
            "If no → is_rule should be low and maps_to=NONE.\n"
            "2. For each canonical label, does the line's MECHANISM (not just vocabulary) match its "
            "description? Pick the best match only if genuinely one exists.\n"
            "3. Is your `evidence` string copy-pasteable from the input with no edits? If you had to "
            "reword it to make it fit, you likely picked the wrong label — reconsider or use NONE.\n\n"
            'Line: "%s"\nJSON:'
        ) % (labels, catalog_brief(), text.replace('"', "'"))
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 300,
        }
        req = urllib.request.Request(
            self.base_url, data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     # HelmCode is behind Cloudflare, which 403s (err 1010) the
                     # default python-urllib UA. A browser-like UA is required.
                     "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) rules_ingestion/0.1"},
        )
        _dbg(f"    → DeepSeek POST {self.base_url}  “{text[:60]}…”")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw = data["choices"][0]["message"]["content"]
            _dbg(f"    ← DeepSeek {raw[:120]}")
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            d = json.loads(m.group() if m else raw)
            label = str(d.get("maps_to", "NONE")).upper()
            if label not in CHOICE_LABELS:
                label = "NONE"
            evidence = str(d.get("evidence") or "")
            # Grounding: evidence must be a substring of the source line (if given)
            # and the label must share lexicon tokens with the text.
            if label != "NONE":
                folded_text = _accent_fold(text)
                folded_ev = _accent_fold(evidence) if evidence else ""
                if evidence and folded_ev not in folded_text:
                    _dbg(f"    ← DeepSeek evidence not in text -> force NONE")
                    label = "NONE"
                elif not lexicon_grounded(text, label):
                    _dbg(f"    ← DeepSeek {label} not lexicon-grounded -> force NONE")
                    label = "NONE"
            return {"is_rule": float(d.get("is_rule", 0.0)), "maps_to": label,
                    "confidence": float(d.get("confidence", 0.0)),
                    "evidence": evidence}
        except Exception as exc:
            _dbg(f"    ← DeepSeek FAILED ({type(exc).__name__}: {exc}) -> degrade")
            return None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def classify_lines(lines: List[dict], prefer_jev: bool = True, use_llm: bool = True,
                   jev: Optional[JEVClient] = None,
                   lexical: Optional[LexicalJEV] = None,
                   llm: Optional[DeepSeekFallback] = None
                   ) -> List[Tuple[dict, ClassifyResult]]:
    """Classify loader lines JEV-first, degrading to lexical, with DeepSeek fallback.

    prefer_jev=False forces the offline lexical tier (hermetic tests, no network).
    """
    jev = jev if jev is not None else JEVClient()
    lexical = lexical or LexicalJEV()
    llm = llm or DeepSeekFallback()
    out: List[Tuple[dict, ClassifyResult]] = []

    for line in lines:
        text = line["text"]
        declared = bool(line.get("declared"))
        res: Optional[ClassifyResult] = None

        if prefer_jev and jev.available():
            try:
                d = jev.classify(text)
                if declared:
                    route, reason = route_declared(d["maps_to"], d["maps_to_conf"], "jev")
                else:
                    route, reason = route_for(d["is_rule"], d["maps_to"], d["maps_to_conf"], "jev")
                res = ClassifyResult(text, d["is_rule"], d["maps_to"], d["maps_to_conf"],
                                     route, "jev", d.get("probabilities", {}), reason,
                                     usage=d.get("usage"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
                res = None  # degrade below

        if res is None:
            res = lexical.classify(text)
            if declared:
                res.route, res.reason = route_declared(res.maps_to, res.maps_to_conf,
                                                       "lexical", ACTIVATE_CONF_LEXICAL)
            if prefer_jev and jev.available():   # JEV was tried but failed
                res.method = "jev_lexical_degraded"
                res.reason += " | JEV API unavailable -> lexical degrade"

        if res.route == "LLM_FALLBACK":
            data = llm.classify(text) if use_llm else None
            if data is not None:
                lbl, conf, isr = data["maps_to"], data["confidence"], data["is_rule"]
                if isr >= IS_RULE_HI and lbl != "NONE" and conf >= ACTIVATE_CONF:
                    res.route, res.reason = "ACTIVATE", f"[deepseek] confirmed {lbl} @ {conf:.2f}"
                elif isr >= IS_RULE_HI:
                    res.route, res.reason = "NEW_RULE", "[deepseek] rule-like, no confident canonical match"
                else:
                    res.route, res.reason = "NOT_A_RULE", f"[deepseek] not a rule (is_rule {isr:.2f})"
                res.method, res.maps_to, res.maps_to_conf, res.is_rule = "deepseek", lbl, conf, isr
            else:
                res.route = "NEW_RULE"
                res.method = res.method + "+degraded"
                res.reason += " | LLM unavailable -> queued for human review"

        out.append((line, res))
    return out
