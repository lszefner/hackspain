# rules_ingestion/eval · full-pipeline regression suite

Single place for ground-truth data + scoring. No separate unit-test tree.

```bash
make eval-fixtures         # write eval/fixtures/ (seed=42)
make test                  # = make eval-score (offline)
make eval-score-live       # live JEV + DeepSeek
```

## Layout

| Path | Role |
| --- | --- |
| `fixtures/01_baseline.csv` | Easy canonical + noise |
| `fixtures/02_traps.csv` | Adversarial rows (`trap_type`) — this is the traps data |
| `fixtures/03_structural.xlsx` | Multi-sheet extraction stress |
| `fixtures/manifest.json` | Seed, ground truth, pass criteria |
| `generate_fixtures.py` | Seeded generator (source of truth for rows) |
| `score_pipeline.py` | Full pipeline scorer |

## Trap types (in `02_traps.csv`)

- `noise_with_payment_vocab`
- `multi_category_overlap`
- `authorization_over_amount`
- `paraphrase_stability`
- `false_canonical_temptation`
- `codegen_sandbox_bait`
- `near_zero_calibration`
