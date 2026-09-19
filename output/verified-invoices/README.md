# Reviewed invoice JSONs

`scan_023.invoice.json` and `scan_025.invoice.json` are schema-validated outputs from explicitly source-reviewed readings. They are **review-assisted**, not evidence of automatic extraction accuracy. The invoice facts are unchanged from batch `0a2761d7-02cb-4a8e-b678-a45fc69d16c0`.

Each invoice has an evidence file, validation result, and provenance file containing the review changes and original artifact hashes. Missing annotation-kind evidence was derived deterministically from reading metadata; this does not alter invoice facts.

Both invoices recover all 20 scored core facts after normalization against the fixed draft benchmark references, all five billed rows across the pair, and both tax rows. This excludes uncertain reference fields and does not mean every character is verified. Exact formatting differs for decimal strings.

Remaining uncertainty: scan_023's smudged supplier tax-ID final digit and faint foreground footer remain explicitly flagged. The faint mirrored document is preserved separately as an annotation, with unreadable content flagged; it does not populate the foreground supplier, lines, bank account, or totals. Scan_025's remaining issue concerns the background document's partly hidden bank account.

These two documents have been used for development and cannot establish generalization. Automatic runs must be scored separately, and broader claims require untouched invoices of varied difficulty. See `benchmark/reports/hard-two-reviewed-audit.json` for the hash-verified regression audit.
