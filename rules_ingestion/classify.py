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

from .catalog import CATALOG, CHOICE_LABELS, all_lexicons
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


_load_dotenv()


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
_CHOICE_CRITERIA = {
    "VENDOR": "El proveedor/NIF debe existir en el maestro y el IBAN de la factura debe coincidir con el del maestro.",
    "DUPLICATES": "No pagar dos veces: misma factura/proveedor, mismo importe+fecha, o pedido ya PAGADO/estado no PENDIENTE.",
    "AMOUNT": "Importes, IVA y total (base+IVA) correctos, en tolerancia, y coincidentes con el importe del pedido.",
    "AUTHORIZATION": "Requiere aprobación humana cuando el importe supera un umbral establecido.",
    "DATES": "La fecha de la factura debe ser válida, no futura y dentro del plazo de pago del proveedor.",
    "MISSING": "Faltan campos obligatorios (NIF/IBAN/pedido/importe/IVA/fecha), o hay una anomalía/duda que debe escalarse a un humano.",
    "NONE": "No corresponde a ninguna regla canónica anterior (posible regla nueva).",
}


def noul_question() -> dict:
    # Invoice-domain-specific: this gates DISCOVERY (declared sources bypass Noul).
    # Broad enough to include VALIDATION rules (importes/IVA/fechas/campos), narrow
    # enough to reject workplace noise (horarios, logística, recados).
    return {
        "type": "noul",
        "instructions": ("¿Es esta línea una regla o condición para procesar o pagar facturas de "
                         "proveedores (validar proveedor, importes, IVA, fechas, duplicados, "
                         "campos obligatorios o autorizaciones)?"),
        "criteria": {
            "true": "Es una norma/condición sobre facturas de proveedores o su pago (incluida su validación)",
            "false": "No trata sobre facturas de proveedores ni su pago (p.ej. horarios, logística, recados, actas)",
        },
    }


def choice_question() -> dict:
    return {
        "type": "choice",
        "instructions": "¿A qué regla canónica de pago a proveedores corresponde esta línea?",
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
_DEONTIC = {
    "debe", "deben", "debera", "pagar", "escalar", "nunca", "solo", "requiere",
    "coincida", "coincide", "prohibido", "obligatorio", "valida", "valido",
    "permitido", "existir", "pertenecer",
}
_DOMAIN = {
    "nif", "iban", "pedido", "importe", "iva", "fecha", "proveedor", "estado",
    "maestro", "total", "base", "factura", "moneda", "tolerancia", "pendiente",
}
_NOTE_NEG = {
    "acordarse", "preguntar", "roto", "llaman", "cafe", "planta", "sonia",
    "viernes", "mirar", "hueco", "aplica", "recordar", "avisar", "ojo",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LEADING_NUM_RE = re.compile(r"^\s*\d+\s*[.)\-]")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def tokenize(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(_accent_fold(text)) if len(t) >= 2]


class Noul:
    """Lexical binary proposition: P(text is a rule)."""

    @staticmethod
    def is_rule(text: str) -> float:
        toks = set(tokenize(text))
        words = tokenize(text)
        score = -1.0
        if _LEADING_NUM_RE.match(text):
            score += 2.2
        if toks & _DEONTIC:
            score += 1.3
        domain_hits = len(toks & _DOMAIN)
        if domain_hits:
            score += 1.1 + 0.5 * min(domain_hits - 1, 2)
        if len(words) >= 6:
            score += 0.4
        if toks & _NOTE_NEG:
            score -= 2.0
        if len(words) < 4 and not domain_hits:
            score -= 0.8
        return round(_sigmoid(score), 4)


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
        ordered = sorted(scores.values(), reverse=True)
        top1, top2 = ordered[0], (ordered[1] if len(ordered) > 1 else 0.0)
        conf = round(top1 / (top1 + top2), 4) if (top1 + top2) else 0.0
        return max(scores, key=scores.get), conf, scores


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
            "Clasificador de reglas de pago a proveedores.\n"
            "Responde SOLO JSON con las claves: is_rule (0..1), "
            "maps_to (uno de: %s), confidence (0..1).\n"
            "maps_to=NONE si no encaja en ninguna regla canónica.\n\n"
            'Línea: "%s"\nJSON:' % (labels, text.replace('"', "'"))
        )
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 200,
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
            return {"is_rule": float(d.get("is_rule", 0.0)), "maps_to": label,
                    "confidence": float(d.get("confidence", 0.0))}
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
