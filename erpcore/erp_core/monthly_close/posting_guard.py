# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Serialization gate between ledger postings and hard close.

Every GL Entry and Stock Ledger Entry in ERPNext v16 is created through
`general_ledger.make_entry` / `stock_ledger.make_entry`, which call `.submit()`
on the ledger row. Hooking `before_submit` on those two doctypes therefore
covers invoices, payments, journals, stock vouchers, landed costs, scheduled
depreciation, deferred accounting, exchange-rate revaluation, cancellation
reversals and reposting with one code path.

The gate is the Monthly Close row for (company, month start), which has a
unique key:

* A posting dated in a month that has already ended takes a shared lock on that
  row (`LOCK IN SHARE MODE`). If no close exists yet, InnoDB takes a gap lock on
  the unique key instead, so the close cannot be created under it either.
* Hard close takes `FOR UPDATE` on the same row. It waits for in-flight postings
  to commit, so its final checks and fingerprint see them. Postings that arrive
  while it holds the lock wait, and then read the new state with a locking read,
  which returns the latest committed row rather than a stale REPEATABLE READ
  snapshot. They are refused.

Postings dated in the current month or later skip the gate entirely, because a
month cannot be hard-closed before it ends. Shared locks do not conflict with
each other, so concurrent postings do not slow each other down.
"""

import frappe
from frappe import _
from frappe.utils import get_first_day, getdate, nowdate

from erpcore.erp_core.monthly_close.constants import LOCKED_STATES
from erpcore.erp_core.monthly_close.periods import month_label


class ClosedMonthError(frappe.ValidationError):
	pass


def guard_ledger_entry(doc, method=None):
	"""`before_submit` hook on GL Entry and Stock Ledger Entry."""
	if not doc.get("company") or not doc.get("posting_date"):
		return

	posting_date = getdate(doc.posting_date)
	month_start = get_first_day(posting_date)
	if month_start >= get_first_day(nowdate()):
		return

	close = acquire_posting_gate(doc.company, month_start)
	if close and close.state in LOCKED_STATES:
		frappe.throw(
			_(
				"{0} {1} is dated {2}, but {3} for {4} is {5}. Reopen the close before posting into that month."
			).format(
				_(doc.voucher_type or doc.doctype),
				frappe.bold(doc.voucher_no or doc.name),
				frappe.format(posting_date, "Date"),
				frappe.bold(close.name),
				month_label(month_start),
				_(close.state),
			),
			ClosedMonthError,
			title=_("Month Closed"),
		)


def acquire_posting_gate(company: str, month_start):
	"""Shared lock on the company/month gate. Returns the close row, if any."""
	rows = frappe.db.sql(
		"""
		select name, state
		from `tabMonthly Close`
		where company = %(company)s and period_start = %(month_start)s
		lock in share mode
		""",
		{"company": company, "month_start": month_start},
		as_dict=True,
	)
	return rows[0] if rows else None


def acquire_close_gate(close_name: str):
	"""Exclusive lock on one close row. Lock order: closes by ascending period, then Accounting Period."""
	rows = frappe.db.sql(
		"""select name, state, revision from `tabMonthly Close` where name = %s for update""",
		close_name,
		as_dict=True,
	)
	if not rows:
		frappe.throw(_("Monthly Close {0} not found.").format(close_name), frappe.DoesNotExistError)
	return rows[0]
