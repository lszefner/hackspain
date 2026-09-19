# Automatic outputs — region vision version 2

These two JSONs come from interpretation batch `530718ca-f59b-4fa5-a8fb-7d8d63e59842`, reusing the exact automatic readings from fresh batch `72730e24-dddc-4f49-8f75-26a084ef6a73`, with no manual reading corrections or reference values supplied to inference. Each region was independently read by two existing Helmcode models and compared against source images, with every first-pass block accounted for.

Both invoices match all 20 scored core facts after normalization (40/40 total), all five billed rows and both tax rows. This excludes uncertain draft-reference fields and annotations. It is not whole-document accuracy or generalization proof. The latest runtime outcomes are `needs_review` for scan_023 and `completed` for scan_025. The latter still has incorrect/incomplete background annotation text, so its status must not be read as proof that every transcribed character is correct.

Known limits: scan_023's smudged tax-ID ends in the reviewed candidate 9 but the automatic output does not flag it; its small note is read as `OK A`, and the faint footer is not character-exact. Background text still includes unsupported readings and is separate from foreground invoice fields. Scan_025's foreground fields are correct against the reviewed comparison, but the background bank account is incomplete and background customer name differs.

The `.reading.json`, `.evidence.json`, `.checks.json`, and `.provenance.json` companions preserve the automatic result and source-export identity. Explicitly review-assisted outputs remain separately in `output/verified-invoices/`.
