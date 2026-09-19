# rules_ingestion · authoring-time rule pipeline

Turns Alberto's rule sources (the free-text norm + our predefined catalog, and
*any* file where rules might hide) into one **versioned, fully-traced ruleset**
that a 100%-deterministic decision layer can later apply to payments.

```
loader ──▶ classify (JEV: Noul is_rule + Choice maps_to) ──▶ store
  │              │  primary: JEV · fallback: DeepSeek · degrade: lexical
sources.yaml   catalog.py + normalize.py                  outcome/<ver>/rules.json
```

> **Key idea:** the classifier runs here, at *authoring* time. A payment
> decision is a pure function of the frozen `outcome/<ver>/rules.json`. Same input →
> same output. A provider outage can never change a decision — only which tier
> authored a rule (recorded in the trace).

Generated JSON always lands in **`outcome/<ruleset_version>/rules.json`** (e.g. `outcome/v3/rules.json`).
Hand-authored config stays here (`catalog.py`, `rules_v3.yaml`, `sources.yaml`).
Saturday v4 = new profile + `outcome/v4/rules.json`; impact analysis is a file diff.

## Classifier tiers (in order)

1. **JEV — TypeSafe System One** (primary). `Noul(is_rule)` + `Choice(maps_to)`
   in one `POST /v1/systemone` call. Calibrated confidence, no hallucination.
2. **DeepSeek** (LLM fallback). Only for lines JEV can't clear the **0.8**
   confidence gate on. OpenAI-compatible endpoint over stdlib HTTP.
3. **Lexical** (offline degrade). If the JEV API is unreachable the pipeline
   still runs — deterministic, dependency-free — so authoring is never blocked.

Confidence gate: a canonical mapping must be **≥ 0.80** to activate without a
human/LLM in the loop; below that it routes to DeepSeek; if DeepSeek is
unavailable the line is flagged for human review (never silently activated).

## Declared vs. discovery — two ways in

- **Declared source** (`Norma_Pagos_v*`, `declared_rules: true`): every line IS
  a rule. The classifier **skips Noul** and routes on `Choice(maps_to)` only.
- **Discovery** (`--scan FOLDER`): rules may be *hidden in random text across
  different sheets/files*. Here the full `Noul(is_rule)` gate runs first (it is
  payment-specific, so workplace noise like "parking cierra a las 22h" is
  rejected), then `Choice(maps_to)` decides canonical vs. NEW.

## Run

```bash
make rules            # build from the norm via JEV (+ DeepSeek fallback)
make rules-offline    # build fully offline (lexical tier, no network, no keys)
make scan             # discovery over rules_ingestion/eval/fixtures/
make test             # = make eval-score (offline full-pipeline eval)
make eval-score-live  # same with live JEV + DeepSeek
```

Secrets come from the process environment: `JEV_API_KEY` (required for JEV) and
the Helmcode credentials the extractor already uses -- `HELMCODE_API_KEY`,
`HELMCODE_BASE_URL` and `HELMCODE_DEEPSEEK_MODEL` -- which enable the DeepSeek
fallback. `DEEPSEEK_MODEL` optionally points rule authoring at a cheaper model
on the same account.

## Results

**Norm (declared) — all 6 activate via JEV, zero fallback:**

| Cell | Route | maps_to | conf |
| --- | --- | --- | --- |
| A2 | ACTIVATE | VENDOR | 1.00 |
| A3/A4 | ACTIVATE | AMOUNT | 1.00 |
| A5 | ACTIVATE | DATES | 1.00 |
| A6 | ACTIVATE | DUPLICATES | 1.00 |
| A7 | ACTIVATE | MISSING | 1.00 |

**Discovery (`make scan`) — rules in `rules_ingestion/eval/fixtures/`:**
scored end-to-end via `make test` / `make eval-score-live` (see `eval/README.md`).

## Files

| File | Role |
| --- | --- |
| `sources.yaml` | file→sheet→column contract; whitelists real sheets, names the junk, marks the norm as a declared source, records that the **ERP** owns `PENDIENTE/PAGADA`. |
| `catalog.py` | the 6 canonical rules + Choice labels/lexicons. |
| `rules_v3.yaml` | the **norm profile**: enabled rules, params, precedence. `AUTHORIZATION` off. v4 = a new profile → impact analysis is a diff. |
| `normalize.py` | Decimal / IBAN / NIF(+control) / date / whitespace. |
| `loader.py` | reads the workbook, dedups (P007), discovers schema, extracts norm lines; `scan_candidates()` for discovery. |
| `classify.py` | JEVClient + DeepSeekFallback + LexicalJEV + routing. |
| `store.py` | internal catalog+norm assembly (fed into merge). |
| `merge.py` | schema 2.0 merge → single public outcome. |
| `build_rules.py` | orchestrates build / scan into `outcome/<ver>/rules.json`. |
| `eval/` | fixtures (baseline + traps + structural) + full-pipeline scorer. |
| `outcome/<ver>/rules.json` | **THE outcome** (canonical + discovered NEW, versioned). |

## Trace (per rule)

`source` · `ruleset_version` · `matched_from` (sheet/cell/text) ·
`is_rule_conf` · `maps_to_conf` · `method` (jev / deepseek / lexical / *_degraded) ·
`on_fail` · `enabled`.
