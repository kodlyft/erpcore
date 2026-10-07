# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""A content hash of the financial state a close was checked and approved against.

A GL row count or the latest `modified` timestamp would miss a repost that
rewrites values in place, or a cancel-and-amend that keeps the count. Instead
the hash covers aggregated amounts:

* gl:       per account, finance book and opening flag: debit and credit for the
            month, plus balances brought forward from before the month
            (cancelled rows excluded).
* sle:      per item and warehouse: quantity and value change in the month.
* ple:      per account, party type and party: outstanding up to month end
            (drives AR/AP ageing).
* drafts:   count of draft vouchers dated in the month, per doctype.
* reposts:  Repost Item Valuation rows up to month end that have not finished.
* assets:   due depreciation schedule rows that are not posted.
* bank:     bank certifications of this revision.
* policy:   policy version, template snapshot hash and close revision.

Limitations: a direct SQL edit that keeps every aggregate identical is not
detected, and neither are documents that do not post to these ledgers.
"""

import hashlib
import json

import frappe
from frappe.query_builder.functions import Count, Sum
from frappe.utils import flt

from erpcore.erp_core.monthly_close.constants import BANK_CERT_DOCTYPE

DRAFT_VOUCHER_DOCTYPES = (
	"Sales Invoice",
	"Purchase Invoice",
	"Journal Entry",
	"Payment Entry",
	"Stock Entry",
	"Stock Reconciliation",
	"Delivery Note",
	"Purchase Receipt",
	"Landed Cost Voucher",
)


def _digest(value) -> str:
	return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _amount(value) -> str:
	return f"{flt(value, 6):.6f}"


def _gl(company, start, end):
	gle = frappe.qb.DocType("GL Entry")
	movement = (
		frappe.qb.from_(gle)
		.select(gle.account, gle.finance_book, gle.is_opening, Sum(gle.debit), Sum(gle.credit), Count("*"))
		.where(gle.company == company)
		.where(gle.is_cancelled == 0)
		.where(gle.posting_date[start:end])
		.groupby(gle.account, gle.finance_book, gle.is_opening)
		.run()
	)
	opening = (
		frappe.qb.from_(gle)
		.select(gle.account, gle.finance_book, Sum(gle.debit) - Sum(gle.credit))
		.where(gle.company == company)
		.where(gle.is_cancelled == 0)
		.where(gle.posting_date < start)
		.groupby(gle.account, gle.finance_book)
		.run()
	)
	return {
		"movement": sorted(
			[a, fb or "", op or "", _amount(dr), _amount(cr), int(n)] for a, fb, op, dr, cr, n in movement
		),
		"opening": sorted([a, fb or "", _amount(bal)] for a, fb, bal in opening),
	}


def _sle(company, start, end):
	sle = frappe.qb.DocType("Stock Ledger Entry")
	rows = (
		frappe.qb.from_(sle)
		.select(
			sle.item_code, sle.warehouse, Sum(sle.actual_qty), Sum(sle.stock_value_difference), Count("*")
		)
		.where(sle.company == company)
		.where(sle.is_cancelled == 0)
		.where(sle.posting_date[start:end])
		.groupby(sle.item_code, sle.warehouse)
		.run()
	)
	return sorted([i, w, _amount(q), _amount(v), int(n)] for i, w, q, v, n in rows)


def _ple(company, end):
	ple = frappe.qb.DocType("Payment Ledger Entry")
	rows = (
		frappe.qb.from_(ple)
		.select(ple.account, ple.party_type, ple.party, Sum(ple.amount), Count("*"))
		.where(ple.company == company)
		.where(ple.delinked == 0)
		.where(ple.posting_date <= end)
		.groupby(ple.account, ple.party_type, ple.party)
		.run()
	)
	return sorted([a, pt or "", p or "", _amount(amt), int(n)] for a, pt, p, amt, n in rows)


def _drafts(company, start, end):
	counts = {}
	for doctype in DRAFT_VOUCHER_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			counts[doctype] = frappe.db.count(
				doctype, {"company": company, "docstatus": 0, "posting_date": ["between", [start, end]]}
			)
	return counts


def _reposts(company, end):
	return sorted(
		frappe.get_all(
			"Repost Item Valuation",
			filters={
				"company": company,
				"docstatus": 1,
				"posting_date": ["<=", end],
				"status": ["in", ["Queued", "In Progress", "Failed"]],
			},
			pluck="name",
		)
	)


def _assets(company, end):
	schedule = frappe.qb.DocType("Depreciation Schedule")
	ads = frappe.qb.DocType("Asset Depreciation Schedule")
	rows = (
		frappe.qb.from_(schedule)
		.inner_join(ads)
		.on(schedule.parent == ads.name)
		.select(schedule.name)
		.where(ads.company == company)
		.where(ads.docstatus == 1)
		.where(ads.status == "Active")
		.where(schedule.schedule_date <= end)
		.where(schedule.journal_entry.isnull() | (schedule.journal_entry == ""))
		.run(pluck=True)
	)
	return sorted(rows)


def _bank(close):
	return sorted(
		frappe.get_all(
			BANK_CERT_DOCTYPE,
			filters={"monthly_close": close.name, "revision": close.revision},
			fields=["name", "status", "statement_balance", "statement_date", "unexplained_difference"],
			as_list=True,
		)
	)


def compute(close) -> tuple[str, dict]:
	"""Return (overall hash, per-component hashes) for the close's company and month."""
	company, start, end = close.company, close.period_start, close.period_end
	components = {
		"gl": _digest(_gl(company, start, end)),
		"sle": _digest(_sle(company, start, end)),
		"ple": _digest(_ple(company, end)),
		"drafts": _digest(_drafts(company, start, end)),
		"reposts": _digest(_reposts(company, end)),
		"assets": _digest(_assets(company, end)),
		"bank": _digest(_bank(close)),
		"policy": _digest([close.policy_version, _digest(close.template_snapshot or ""), close.revision]),
	}
	return _digest(components), components


def changed_components(old: dict | str | None, new: dict) -> list[str]:
	if isinstance(old, str):
		old = json.loads(old or "{}")
	old = old or {}
	return sorted(key for key in new if old.get(key) != new[key])
