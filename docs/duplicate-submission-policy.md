# Duplicate submissions and audit records

New backend evaluations include every previously evaluated submission in the
processed-history snapshot, including records with the same filename. Both
context builders honor the frozen `same_file_policy: include` marker.

With the enabled `DUPLICATES` rule, an evidenced match on invoice number and
resolved supplier produces `DUPLICATE_SUBMISSION` and `NO_PAGAR`. This is evidence
of an earlier submission, not evidence that payment happened. Filename equality
alone does not prove a duplicate; amount/date-only matches remain review cases.

- Reusing a request key returns its stored run. It does not create a submission.
- A new request key creates a new submission, and earlier evaluations remain in
  duplicate history even if the filename is unchanged.
- Review artifacts are linked assessments, not independent submissions. History
  queries use evaluation records only; a review cannot double-count its parent.
- An interrupted extraction without an evaluation is not a processed-history
  record. Completing it still requires evaluation against the available history.
- Historical snapshots without the marker retain their original filename
  exclusion during forensic replay. Existing evaluations are not rewritten.

This fixes the same-filename exclusion. The guarantee is against prior committed
evaluations visible in the captured history, not global exclusivity across
simultaneous independent submissions. An atomic cross-process submission claim
would be a separate change. Incomplete identity evidence must not be invented to
establish a match or obtain approval.

The backend and rule tests cover same-name and renamed resubmissions, request-key
replay, preserved historical results, and a different invoice sharing a filename.
They run on disposable Postgres with mocked providers. Restart the API after
updating the code; already-running batch processes retain their loaded code.
