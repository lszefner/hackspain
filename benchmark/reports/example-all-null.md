# Invoice benchmark: example-all-null-c4bcafc4

Track: **isolated**. Provider/model: `import` / `SYNTHETIC-ALL-NULL-NOT-A-PROVIDER`.

Selected: 50. Human-reviewed coverage: 0/50.

Draft-reference results are **PROVISIONAL**. No reviewed invoices means no official accuracy result.

| Reference class | Documents | Scorable | Schema valid | Main metric | Whole-document correct |
|---|---:|---:|---:|---:|---:|
| official | 0 | 0 | unavailable | unavailable | unavailable |
| provisional | 50 | 50 | 1.0000 | 0.0000 | 0.0000 |

Main metric: macro present-value recall (interpretation), or macro token content recall (OCR). Correct-null rates are reported separately in JSON.

## Operations

```json
{
  "known_cost_usd": "0",
  "cost_known_documents": 50,
  "cost_unknown_documents": 0,
  "total_cost_usd": "0",
  "known_latency_seconds": 0,
  "latency_known_documents": 50,
  "usage_totals": {},
  "attempts": 0
}
```

## Per-document results

| File | Split | Reference | Output | Main metric | Exclusions |
|---|---|---|---|---:|---:|
| 2026-01-08_P001.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| 2026-01-11_P007.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| 2026-01-12_P002.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| 2026-01-12_P010.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| 2026-01-14_P002.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| 2026-01-15_P003.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| 2026-01-16_P004.pdf | held_out | draft_or_unavailable | success | 0.0000 | 0 |
| 2026-01-17_P011.pdf | held_out | draft_or_unavailable | success | 0.0000 | 0 |
| 2026-01-18_P005.pdf | held_out | draft_or_unavailable | success | 0.0000 | 0 |
| 2026-01-22_P006.pdf | held_out | draft_or_unavailable | success | 0.0000 | 0 |
| FA-3788_informática.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| FA-3939_transportes.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| FA-4125_transportes.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| FA-4290_mensajería.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| FA-4294_papelería.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| FA-4349_electricidad.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| FA-4385_informática.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| FA-4488_transportes.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| FA-4634_seguridad.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| FA-4666_transportes.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| FA-4673_informática.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| FA-4813_seguridad.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| FA-4816_seguridad.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| FA-4819_informática.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| FA-4962_mensajería.pdf | held_out | draft_or_unavailable | success | 0.0000 | 0 |
| FA-5044_mensajería2.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| FA-5077_electricidad.pdf | held_out | draft_or_unavailable | success | 0.0000 | 0 |
| FA-5197_papelería.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| FA-5377_electricidad.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| FA-5418_catering.pdf | held_out | draft_or_unavailable | success | 0.0000 | 0 |
| scan_007.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| scan_008.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| scan_009.pdf | development | draft_or_unavailable | success | 0.0000 | 2 |
| scan_010.pdf | development | draft_or_unavailable | success | 0.0000 | 2 |
| scan_011.pdf | development | draft_or_unavailable | success | 0.0000 | 2 |
| scan_012.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| scan_013.pdf | development | draft_or_unavailable | success | 0.0000 | 2 |
| scan_014.pdf | development | draft_or_unavailable | success | 0.0000 | 3 |
| scan_015.pdf | development | draft_or_unavailable | success | 0.0000 | 0 |
| scan_016.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| scan_017.pdf | development | draft_or_unavailable | success | 0.0000 | 4 |
| scan_018.pdf | development | draft_or_unavailable | success | 0.0000 | 1 |
| scan_021.pdf | held_out | draft_or_unavailable | success | 0.0000 | 2 |
| scan_022.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| scan_023.pdf | held_out | draft_or_unavailable | success | 0.0000 | 4 |
| scan_025.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| scan_026.pdf | held_out | draft_or_unavailable | success | 0.0000 | 0 |
| scan_027.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| scan_028.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |
| scan_029.pdf | held_out | draft_or_unavailable | success | 0.0000 | 1 |

## Limitations

- Codex references are drafts, never human-verified ground truth.
- Missing/failed/invalid outputs remain in document denominators. Unavailable references are reported, never treated as absent content.
- No extrapolation to all 500 invoices. Selection is positional, not random.
- Candidate coverage is an optimistic value-level upper bound; grouping/role errors still count.
- No LLM judge. Unsupported values mean contradicted by complete reference null, not independently proven hallucinations.
- Ordering and layout are not equivalent to content coverage. See README for alignment limits.

Full per-field errors, row alignment, exclusions, strict/normalized metrics, split aggregates and reviewed filenames are in the adjacent JSON report.
