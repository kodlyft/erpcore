# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""The fingerprint format of releases before the October 2026 hardening (format 1).

Months closed by an earlier release carry format-1 fingerprints. After an
upgrade, revalidating such a month must compare like with like: the current
books are hashed with this exact format-1 algorithm and compared with the
sealed values. Comparing with the current format would report every month as
changed even when nothing changed.

Format 1 is weaker (aggregate amounts rounded to six decimals, no dimensions or
invoice-level allocations; see fingerprint.py). A month gets the current
format's protection once it is reopened and reclosed. Nothing else uses this
module.
"""

import hashlib
import json

import frappe
from frappe.query_builder.functions import Count, Sum
from frappe.utils import flt

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


def is_legacy(components: dict | None) -> bool:
	"""Format-1 components predate the evidence component."""
	return bool(components) and "evidence" not in components


def digest_of(value) -> str:
	return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def rounded(value) -> str:
	return f"{flt(value, 6):.6f}"


def gl_component(company, start, end):
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
			[a, fb or "", op or "", rounded(dr), rounded(cr), int(n)] for a, fb, op, dr, cr, n in movement
		),
		"opening": sorted([a, fb or "", rounded(bal)] for a, fb, bal in opening),
	}


def sle_component(company, start, end):
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
	return sorted([i, w, rounded(q), rounded(v), int(n)] for i, w, q, v, n in rows)


def ple_component(company, end):
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
	return sorted([a, pt or "", p or "", rounded(amt), int(n)] for a, pt, p, amt, n in rows)


def drafts_component(company, start, end):
	counts = {}
	for doctype in DRAFT_VOUCHER_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			counts[doctype] = frappe.db.count(
				doctype, {"company": company, "docstatus": 0, "posting_date": ["between", [start, end]]}
			)
	return counts


def reposts_component(company, end):
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


def assets_component(company, end):
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


def financial_components(close) -> dict:
	"""Format-1 hashes of the books, for the components revalidation compares."""
	company, start, end = close.company, close.period_start, close.period_end
	return {
		"gl": digest_of(gl_component(company, start, end)),
		"sle": digest_of(sle_component(company, start, end)),
		"ple": digest_of(ple_component(company, end)),
		"drafts": digest_of(drafts_component(company, start, end)),
		"reposts": digest_of(reposts_component(company, end)),
		"assets": digest_of(assets_component(company, end)),
	}
