# Configuration and cutover

1. **Monthly Close Settings** – module switch, default template, `max_sample_rows`
   (how many rows a finding shows; findings always identify *all* their rows).
   Disabling the module stops new closes and transitions, and makes queued hard
   closes refuse to run. Existing locks stay in force and are still monitored.
2. **Monthly Close Template** – the checklist. A close copies (freezes) the template
   when it starts; later template edits never change a running close.
3. **Monthly Close Policy** (one per company):
   * **First managed month (cutover)** – months before it are not managed: no close,
     no sequence rule, no revalidation dependency. After the cutover, months close in
     order.
   * **Checks** – enable/disable registered checks, set severity (Warning / Blocker)
     and tolerance. Policies never carry SQL or code.
   * **Suspense accounts** – accounts that should be empty at month end.
   * **Allow self-approval** – off by default; each use is recorded on the close.

## Policy versions and frozen policy

`policy_version` is owned by the server: a value sent through the form, REST or Data
Import is ignored, and the version increments whenever a controlled field, check or
account changes. Each close revision also stores the **full frozen policy**
(`policy_snapshot`: controls, every check with its configuration *and code version*,
accounts) and its hash (`policy_hash`). Check runs record the hash they were evaluated
under; a run is stale if the policy, its version or any check's code version changed,
even if someone edited the policy table directly without bumping the version.

## Installing and upgrading

Installing or upgrading never creates a policy, so no historical month is managed or
locked automatically. See [migration-and-uninstall.md](migration-and-uninstall.md).
