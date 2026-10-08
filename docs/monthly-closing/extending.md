# Extending checks and vouchers

## Register a check

In your app's `hooks.py`:

```python
erpcore_monthly_close_checks = ["my_app.close_checks"]
```

and in `my_app/close_checks.py`:

```python
from erpcore.erp_core.monthly_close.checks.registry import FINDING, PASSED, Finding, register_check


@register_check("my_check", version=1, label="My check", default_severity="Warning")
def my_check(ctx):
	rows = ...  # read-only queries scoped to ctx.company and ctx.period_start/end
	if not rows:
		return Finding(PASSED, "Nothing to review.")
	return Finding(
		FINDING,
		f"{len(rows)} rows need review.",
		count=len(rows),
		samples=rows[: ctx.sample_limit],
		identities=rows,  # every row, so a waiver binds to all of them
	)
```

Rules:

* Checks are read-only. Never post or submit anything.
* Return `identities` with every row of the finding. Without them a finding with more
  rows than samples cannot be waived.
* Set `waivable=False` for integrity invariants that must be fixed.
* Bump `version` when the logic changes: it invalidates runs evaluated with the old
  code and is recorded in each sealed manifest.
* Raise on failure (recorded as Error) or return NOT_APPLICABLE with a reason; never
  return PASSED when prerequisites are missing.

## Register draft voucher types

Every doctype in ERPNext's `period_closing_doctypes` hook is included in the draft
check and fingerprint automatically. Add others with:

```python
erpcore_monthly_close_draft_vouchers = [
	{"doctype": "My Voucher", "date_field": "posting_date", "amount_field": "base_grand_total"},
]
```

The doctype must be submittable and have `company` and the date field. Drafts in any
workflow state are included.

## Posting guard for custom ledgers

Rows of GL, Stock Ledger and Payment Ledger submitted by any app are covered by the
ledger gate. If your app rewrites ledger rows in place (like a repost), add a
`validate` hook that calls `posting_guard.guard_repost`-style logic, and add a row with
a regression test to the Posting Coverage report.
