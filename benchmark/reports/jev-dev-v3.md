# Invoice benchmark: jev-dev-v3

Track: **isolated**. Provider/model: `jev` / `jev-latest`.

Selected: 30. Human-reviewed coverage: 0/30.

Draft-reference results are **PROVISIONAL**. No reviewed invoices means no official accuracy result.

| Reference class | Documents | Scorable | Schema valid | Main metric | Whole-document correct |
|---|---:|---:|---:|---:|---:|
| official | 0 | 0 | unavailable | unavailable | unavailable |
| provisional | 30 | 30 | 1.0000 | 0.8493 | 0.0000 |

Main metric: macro present-value recall (interpretation), or macro token content recall (OCR). Correct-null rates are reported separately in JSON.

## Operations

```json
{
  "known_cost_usd": null,
  "cost_known_documents": 0,
  "cost_unknown_documents": 30,
  "total_cost_usd": null,
  "known_latency_seconds": 208.26996562300337,
  "latency_known_documents": 30,
  "usage_totals": {
    "input_tokens": 2595341.0,
    "output_tokens": 927912.0
  },
  "attempts": 287
}
```

## Per-document results

| File | Split | Reference | Output | Main metric | Exclusions |
|---|---|---|---|---:|---:|
| 2026-01-08_P001.pdf | development | draft_or_unavailable | success | 0.8824 | 1 |
| 2026-01-11_P007.pdf | development | draft_or_unavailable | success | 0.9048 | 1 |
| 2026-01-12_P002.pdf | development | draft_or_unavailable | success | 0.8571 | 1 |
| 2026-01-12_P010.pdf | development | draft_or_unavailable | success | 0.9412 | 1 |
| 2026-01-14_P002.pdf | development | draft_or_unavailable | success | 0.8095 | 0 |
| 2026-01-15_P003.pdf | development | draft_or_unavailable | success | 0.7586 | 0 |
| FA-3788_informática.pdf | development | draft_or_unavailable | success | 0.8261 | 1 |
| FA-3939_transportes.pdf | development | draft_or_unavailable | success | 0.8571 | 0 |
| FA-4125_transportes.pdf | development | draft_or_unavailable | success | 0.7368 | 0 |
| FA-4290_mensajería.pdf | development | draft_or_unavailable | success | 0.7647 | 0 |
| FA-4294_papelería.pdf | development | draft_or_unavailable | success | 0.8500 | 1 |
| FA-4349_electricidad.pdf | development | draft_or_unavailable | success | 0.8571 | 0 |
| FA-4385_informática.pdf | development | draft_or_unavailable | success | 0.8333 | 0 |
| FA-4488_transportes.pdf | development | draft_or_unavailable | success | 0.6786 | 1 |
| FA-4634_seguridad.pdf | development | draft_or_unavailable | success | 0.9130 | 0 |
| FA-4666_transportes.pdf | development | draft_or_unavailable | success | 0.7200 | 1 |
| FA-4673_informática.pdf | development | draft_or_unavailable | success | 0.8077 | 0 |
| FA-4813_seguridad.pdf | development | draft_or_unavailable | success | 0.8077 | 0 |
| scan_007.pdf | development | draft_or_unavailable | success | 0.8824 | 0 |
| scan_008.pdf | development | draft_or_unavailable | success | 0.9375 | 1 |
| scan_009.pdf | development | draft_or_unavailable | success | 0.8667 | 2 |
| scan_010.pdf | development | draft_or_unavailable | success | 0.9412 | 2 |
| scan_011.pdf | development | draft_or_unavailable | success | 0.9412 | 2 |
| scan_012.pdf | development | draft_or_unavailable | success | 0.8500 | 1 |
| scan_013.pdf | development | draft_or_unavailable | success | 0.8947 | 2 |
| scan_014.pdf | development | draft_or_unavailable | success | 0.8333 | 3 |
| scan_015.pdf | development | draft_or_unavailable | success | 0.8421 | 0 |
| scan_016.pdf | development | draft_or_unavailable | success | 0.9444 | 1 |
| scan_017.pdf | development | draft_or_unavailable | success | 0.8889 | 4 |
| scan_018.pdf | development | draft_or_unavailable | success | 0.8519 | 1 |

## Limitations

- Codex references are drafts, never human-verified ground truth.
- Missing/failed/invalid outputs remain in document denominators. Unavailable references are reported, never treated as absent content.
- No extrapolation to all 500 invoices. Selection is positional, not random.
- Candidate coverage is an optimistic value-level upper bound; grouping/role errors still count.
- No LLM judge. Unsupported values mean contradicted by complete reference null, not independently proven hallucinations.
- Ordering and layout are not equivalent to content coverage. See README for alignment limits.

Full per-field errors, row alignment, exclusions, strict/normalized metrics, split aggregates and reviewed filenames are in the adjacent JSON report.
