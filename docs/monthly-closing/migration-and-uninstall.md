# Migration and uninstall

## Upgrade

`bench --site <site> migrate`:

* adds the new fields (policy hash/snapshot, job tracking, evidence hashes, claim
  tokens, waivable flags);
* runs `monthly_close_company_scoped_period_names`, which renames the `period_name` of
  Accounting Periods **owned by a close** to `Monthly Close YYYY-MM <Company>` so that two
  companies can close the same month. External periods are never touched; document
  names do not change;
* runs `monthly_close_backfill_evidence_hashes`, which hashes evidence attached before
  the upgrade when it resolves to an existing private File, and records the
  certified-content hash of already certified bank workpapers from their values at
  upgrade time. Evidence that does not resolve is left unhashed and shows up as missing;
* re-runs the idempotent installer (roles, unique keys after duplicate checks,
  indexes, one-time seeds).

After upgrading:

* **Check runs from before the upgrade are stale.** Every built-in check's code version
  changed and runs had no frozen policy hash, so closes that were Ready for Review or
  Approved must be sent back, have their checks rerun and be resubmitted. This is
  intended: their results came from the old check logic.
* Open closes freeze the current policy on their next **Run Checks**.
* Revisions closed before the upgrade keep their report snapshots but have no sealed
  manifest/packet; they are not regenerated.

## Uninstall

Uninstalling is refused while any close holds an active owned Accounting Period. For
each such month decide:

* keep it locked – a System Manager uses **Detach Lock**, which hands the period to
  native ERPNext (still enabled, no longer owned);
* unlock it – reopen the close.

Then uninstall again.
