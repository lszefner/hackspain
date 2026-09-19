# outcome · one artifact per ruleset version × policy

```text
outcome/
  README.md
  v3/
    rules.discovered.json      ← shared discovery cache (gitignored)
    balanced/rules.json        ← default posture
    strict/rules.json
    conservative/rules.json
  v4/
    balanced/rules.json
    ...
```

| Command | Effect |
| --- | --- |
| `make rules` | Build `outcome/v3/balanced/rules.json` |
| `make rules POLICY=strict` | Build strict posture |
| `make rules-all-policies` | Build all three offline |
| `make scan POLICY=…` | Fold discovery into that policy's `rules.json` |

Each `rules.json` embeds `policy_id` + `metrics` (fingerprint for future UI).
Compare policies later with `policy_metrics.diff_metrics(a, b)` — no UI yet.
