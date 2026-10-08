# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Serialization gate between ledger mutations and hard close.

Hooked paths (see hooks.py and `monthly_close_posting_coverage` for the matrix):

* GL Entry, Stock Ledger Entry, Payment Ledger Entry and Advance Payment Ledger
  Entry `before_submit`. ERPNext creates every row of these ledgers through
  `.submit()` (`general_ledger.make_entry`, `stock_ledger.make_entry`,
  `accounts.utils.create_payment_ledger_entry`), including cancellation
  reversals, reconciliation allocations (`reconcile_against_document`) and
  ledger reposts. PLE/APLE delinking (`delink_original_entry`) is a raw UPDATE,
  but ERPNext always runs it immediately before submitting the reversal row in
  the same transaction, so refusing the reversal rolls the delink back too.
* Repost Item Valuation, Repost Accounting Ledger and Repost Payment Ledger
  `validate`. A repost rewrites existing ledger rows in place
  (`stock_ledger.update_sle_valuation_fields` uses `db_update`), so no new
  ledger row is submitted for the in-place change. These are refused up front
  when the repost would start on or before the end of a locked month.

Cumulative effects on later months:

* A Stock Ledger Entry dated in an open month revalues every later SLE of the
  same item and warehouse (`update_qty_in_future_sle` and the repost it queues).
  If any of those later SLEs is in a locked month, the posting is refused.
* GL and Payment Ledger postings in an open month change only the *opening*
  balances of later months, never their movement. A later month is locked only
  after the earlier month closed (sequence rule after cutover), so this can
  happen only after the earlier month was reopened; reopening flags every
  later Closed month for revalidation, and revalidation compares the closing
  fingerprint (which includes balances brought forward). See closing.py.

The gate is the Monthly Close row for (company, month start), which has a
unique key:

* A posting dated in a month that has already ended takes a shared lock on that
  row (`LOCK IN SHARE MODE`). If no close exists yet, InnoDB takes a gap lock on
  the unique key instead, so the close cannot be created under it either.
  Stock postings also take shared locks on later locked months they would
  revalue, in ascending month order.
* Hard close takes `FOR UPDATE` on the same row. It waits for in-flight postings
  to commit, so its final checks and fingerprint see them. Postings that arrive
  while it holds the lock wait, and then read the new state with a locking read,
  which returns the latest committed row rather than a stale REPEATABLE READ
  snapshot. They are refused.

Lock order everywhere: Monthly Close rows of one company in ascending month
order, then Accounting Periods. Reopen takes the reopened row and then later
rows ascending; postings take the posting month and then later rows ascending;
hard close takes only its own row. No cycle is possible.

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


def refuse(what: str, posting_date, close, extra: str = ""):
	frappe.throw(
		_(
			"{0} is dated {1}, but {2} for {3} is {4}. Reopen the close before posting into that month."
		).format(
			what,
			frappe.format(getdate(posting_date), "Date"),
			frappe.bold(close.name),
			month_label(close.period_start),
			_(close.state),
		)
		+ (" " + extra if extra else ""),
		ClosedMonthError,
		title=_("Month Closed"),
	)


def gated_month(posting_date):
	"""Month start whose gate a posting must pass, or None for the current month or later."""
	month_start = get_first_day(getdate(posting_date))
	if month_start >= get_first_day(nowdate()):
		return None
	return month_start


def guard_ledger_entry(doc, method=None):
	"""`before_submit` hook on GL Entry, Stock Ledger Entry and Payment Ledger Entry."""
	if not doc.get("company") or not doc.get("posting_date"):
		return

	month_start = gated_month(doc.posting_date)
	if not month_start:
		return

	what = "{0} {1}".format(
		_(doc.get("voucher_type") or doc.doctype), frappe.bold(doc.get("voucher_no") or doc.name)
	)
	close = acquire_posting_gate(doc.company, month_start)
	if close and close.state in LOCKED_STATES:
		refuse(what, doc.posting_date, close)

	if doc.doctype == "Stock Ledger Entry":
		guard_later_stock_months(doc, what)


def guard_advance_ledger_entry(doc, method=None):
	"""`before_submit` on Advance Payment Ledger Entry, which carries no posting date of its own."""
	if not doc.get("company") or not doc.get("voucher_type") or not doc.get("voucher_no"):
		return
	posting_date = frappe.db.get_value(doc.voucher_type, doc.voucher_no, "posting_date")
	if not posting_date:
		return
	guard_ledger_entry(
		frappe._dict(
			doctype=doc.doctype,
			company=doc.company,
			posting_date=posting_date,
			voucher_type=doc.voucher_type,
			voucher_no=doc.voucher_no,
		)
	)


def guard_later_stock_months(sle, what: str):
	"""Refuse a stock posting that would revalue SLEs of a later locked month."""
	for close in locked_closes_after(sle.company, sle.posting_date):
		affected = frappe.db.exists(
			"Stock Ledger Entry",
			{
				"company": sle.company,
				"item_code": sle.get("item_code"),
				"warehouse": sle.get("warehouse"),
				"is_cancelled": 0,
				"posting_date": ["between", [close.period_start, close.period_end]],
			},
		)
		if affected:
			refuse(
				what,
				sle.posting_date,
				close,
				_("It would revalue stock ledger entries of {0} for item {1} in {2}.").format(
					month_label(close.period_start), sle.get("item_code"), sle.get("warehouse")
				),
			)


def locked_closes_after(company: str, posting_date) -> list:
	"""Locked closes ending on or after the date, share-locked in ascending month order."""
	month_start = get_first_day(getdate(posting_date))
	return frappe.db.sql(
		"""
		select name, state, period_start, period_end
		from `tabMonthly Close`
		where company = %(company)s and period_start > %(month_start)s and state in %(states)s
		order by period_start asc
		lock in share mode
		""",
		{"company": company, "month_start": month_start, "states": LOCKED_STATES},
		as_dict=True,
	)


def guard_repost(doc, method=None):
	"""`validate` hook on Repost Item Valuation / Repost Accounting Ledger / Repost Payment Ledger.

	A repost rewrites existing ledger rows in place, so no new ledger row is
	submitted for the change. Refuse it before it is queued if it would touch a
	locked month:

	* Repost Accounting Ledger rebuilds the GL of the listed vouchers only, at
	  their own dates.
	* Repost Item Valuation revalues the affected item/warehouse SLEs from its
	  date onwards, and the GL of every voucher behind them.
	* Repost Payment Ledger rebuilds the Payment Ledger of every voucher from its
	  date onwards.
	"""
	if doc.docstatus == 2 or not doc.get("company"):
		return

	label = "{0} {1}".format(_(doc.doctype), frappe.bold(doc.name or _("(new)")))

	if doc.doctype == "Repost Accounting Ledger":
		for row in doc.get("vouchers") or []:
			if not (row.voucher_type and row.voucher_no):
				continue
			posting_date = frappe.db.get_value(row.voucher_type, row.voucher_no, "posting_date")
			refuse_if_month_locked(doc.company, posting_date, label)
		return

	start = doc.get("posting_date")
	if not start:
		return
	refuse_if_month_locked(doc.company, start, label)

	later = locked_closes_after(doc.company, start)
	if not later:
		return

	if doc.doctype == "Repost Payment Ledger":
		refuse(
			label,
			start,
			later[0],
			_("It rebuilds the Payment Ledger of later months, including {0}.").format(
				month_label(later[0].period_start)
			),
		)

	pairs = repost_item_warehouses(doc)
	for close in later:
		for item_code, warehouse in pairs:
			filters = {
				"company": doc.company,
				"is_cancelled": 0,
				"posting_date": ["between", [close.period_start, close.period_end]],
			}
			if item_code:
				filters["item_code"] = item_code
			if warehouse:
				filters["warehouse"] = warehouse
			if frappe.db.exists("Stock Ledger Entry", filters):
				refuse(
					label,
					start,
					close,
					_("It would revalue stock ledger entries of {0} for item {1}.").format(
						month_label(close.period_start), item_code
					),
				)


def refuse_if_month_locked(company: str, posting_date, label: str):
	month_start = gated_month(posting_date) if posting_date else None
	if not month_start:
		return
	close = acquire_posting_gate(company, month_start)
	if close and close.state in LOCKED_STATES:
		refuse(label, posting_date, close)


def repost_item_warehouses(doc) -> list[tuple[str, str | None]]:
	"""Item/warehouse pairs a Repost Item Valuation revalues."""
	if doc.get("based_on") == "Item and Warehouse" or not doc.get("voucher_no"):
		return [(doc.item_code, doc.get("warehouse"))] if doc.get("item_code") else [(None, None)]

	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={"voucher_type": doc.voucher_type, "voucher_no": doc.voucher_no},
		fields=["item_code", "warehouse"],
		distinct=True,
	)
	# Without ledger rows (e.g. recreate) the scope is unknown: fail closed on the whole company.
	return [(r.item_code, r.warehouse) for r in rows] or [(None, None)]


def acquire_posting_gate(company: str, month_start):
	"""Shared lock on the company/month gate. Returns the close row, if any."""
	rows = frappe.db.sql(
		"""
		select name, state, period_start, period_end
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
		"""select name, state, revision, company, period_start from `tabMonthly Close` where name = %s for update""",
		close_name,
		as_dict=True,
	)
	if not rows:
		frappe.throw(_("Monthly Close {0} not found.").format(close_name), frappe.DoesNotExistError)
	return rows[0]
