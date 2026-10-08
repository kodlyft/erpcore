# Close, reopen and revalidation

## Checks and freshness

* **Run Checks** enqueues a background run. The worker claims it (token + one-hour
  lease, so duplicate jobs exit), then evaluates every check and the fingerprint inside
  **one database read view**, so the results describe one consistent state of the
  books (`Data Read At` on the run). It writes results only if its claim token is still
  current and the close still has the same revision and frozen policy.
* A run is **stale** when the fingerprint of the books *now* differs from the run's,
  or the policy/check versions changed. Submit, approve and hard close all recompute
  it from the current books.
* **Non-waivable findings:** an unbalanced trial balance, unfinished or failed reposts
  (stock, accounting ledger, payment ledger), errors in Blocker checks, and findings
  whose rows are not fully identified. They must be fixed.
* **Waivers bind to the whole finding:** check, code version, severity, tolerance,
  totals at currency precision and every row of the finding. If any row changes – even
  one outside the displayed sample – the waiver no longer applies.

## Hard close

Request (Close Manager, Approved close, month ended, previous managed month Closed) →
background job:

1. Claim: the job records that it started; a stale token exits.
2. Lock transaction: locking read of the close row (waits for in-flight postings),
   re-authorise the requester (module and policy enabled, still a Close Manager with
   company access), rerun every check, compare the fingerprint with the approved one,
   establish the owned Accounting Period, set **Closing**, commit.
3. Seal transaction: verify lock health and fingerprint again, store report snapshots,
   the revision manifest and the HTML packet, set **Closed**, commit.

A failure in step 2 leaves nothing behind. A failure in step 3 keeps the month locked
(**Closing**) for a manager to **Retry Sealing** or **Abort Close** (which disables only
the Accounting Period this close owns and returns it to Approved).

## Reopen

A request (reason required) changes nothing. Approval by a Close Manager (a different
one unless self-approval is allowed) disables only the owned Accounting Period, marks
the revision's snapshots and packets **Superseded**, starts a new revision, sends
later months under review back to In Progress and flags later **Closed** months for
revalidation. Later locks stay in force. Lock order: the reopened close, then later
closes in ascending month order, then Accounting Periods.

## Revalidation of later closed months

**Confirm Revalidation** (Close Reviewer or Manager, with a written comment, not the
user who hard-closed the month) succeeds only when:

1. every earlier managed month is Closed, itself revalidated and its lock healthy;
2. a fresh synchronous check run against the locked month has no blockers;
3. the books (GL incl. balances brought forward, SLE, Payment Ledger, drafts, reposts,
   depreciation) are identical to the sealed closing fingerprint.

If balances changed, revalidation is refused: reopen and reclose the month, which
seals a new revision packet. A flag is never just cleared.
