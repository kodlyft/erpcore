# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""A content hash of everything a close was checked and approved against.

The hash is computed from the transaction's current read view (see txn.py):
the check runner evaluates checks and the fingerprint in one view, so the
stored fingerprint describes exactly the data the results came from. Freshness
is then decided by recomputing it in a later view; a difference means the
results are stale.

Components (each a SHA-256 of a canonical, sorted structure):

* gl:       per account, finance book, cost center, project, accounting
            dimensions, opening flag and account currency: debit/credit in
            company and account currency and row count for the month; balances
            brought forward per account, finance book and account currency.
            A move between cost centers or dimensions changes it even when
            account totals do not.
* sle:      per item and warehouse: quantity, value change and row count for the
            month, and the opening quantity/value brought forward.
* ple:      per account, party and *invoice* (against voucher) with its due
            date: outstanding as at month end in company and account currency.
            Reallocating a payment between two invoices of one party changes it.
* drafts:   identity, amount, workflow state and modification time of every
            draft voucher dated in the month (all registered voucher types).
* reposts:  unfinished Repost Item Valuation / Accounting Ledger / Payment
            Ledger records.
* assets:   due depreciation schedule rows that are not posted.
* bank:     certified content hash, statement hash and status of each bank
            workpaper of this revision.
* evidence: evidence URL and hash of every task of this revision. (Exceptions
            are not part of the fingerprint: they are judged separately, with
            their own evidence verified, whenever blockers are evaluated.)
* policy:   hash of the frozen policy payload (controls, checks, check code
            versions, accounts), template snapshot hash and close revision.

Amounts are serialised exactly from the database's DECIMAL values, never
rounded to a fixed number of places.

Limitations, documented in docs/monthly-closing/posting-coverage.md: a direct
SQL edit that keeps every component identical is not detected, and neither
are documents that post to none of these ledgers. Computing the hash scans the
company's ledgers; the form dashboard caches its freshness for
DASHBOARD_CACHE_SECONDS, while every transition recomputes it.
"""

import hashlib
import json
from decimal import Decimal

import frappe
from frappe.query_builder.functions import Count, Sum

from erpcore.erp_core.monthly_close.constants import BANK_CERT_DOCTYPE, TASK_DOCTYPE

DASHBOARD_CACHE_SECONDS = 120


def digest_of(value) -> str:
	return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def exact_amount(value) -> str:
	"""Exact, canonical text for a DECIMAL (or float) amount."""
	if value is None:
		return "0"
	number = value if isinstance(value, Decimal) else Decimal(repr(float(value)))
	return format(number.normalize(), "f") if number else "0"


def as_text(value) -> str:
	return "" if value is None else str(value)


def gl_dimensions() -> list[str]:
	"""Report-relevant GL dimensions: cost center, project and every accounting dimension."""
	fields = ["cost_center", "project"]
	try:
		from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
			get_accounting_dimensions,
		)

		fields += [d for d in get_accounting_dimensions() if d not in fields]
	except Exception:
		pass
	meta = frappe.get_meta("GL Entry")
	return [f for f in fields if meta.has_field(f)]


def gl_component(company, start, end):
	gle = frappe.qb.DocType("GL Entry")
	dims = [gle[d] for d in gl_dimensions()]
	group = [gle.account, gle.finance_book, gle.is_opening, gle.account_currency, *dims]
	movement = (
		frappe.qb.from_(gle)
		.select(
			*group,
			Sum(gle.debit),
			Sum(gle.credit),
			Sum(gle.debit_in_account_currency),
			Sum(gle.credit_in_account_currency),
			Count("*"),
		)
		.where(gle.company == company)
		.where(gle.is_cancelled == 0)
		.where(gle.posting_date[start:end])
		.groupby(*group)
		.run()
	)
	opening = (
		frappe.qb.from_(gle)
		.select(
			gle.account,
			gle.finance_book,
			gle.account_currency,
			Sum(gle.debit) - Sum(gle.credit),
			Sum(gle.debit_in_account_currency) - Sum(gle.credit_in_account_currency),
		)
		.where(gle.company == company)
		.where(gle.is_cancelled == 0)
		.where(gle.posting_date < start)
		.groupby(gle.account, gle.finance_book, gle.account_currency)
		.run()
	)
	width = len(group)
	return {
		"movement": sorted(
			[as_text(v) for v in row[:width]]
			+ [exact_amount(v) for v in row[width : width + 4]]
			+ [int(row[-1])]
			for row in movement
		),
		"opening": sorted(
			[as_text(a), as_text(fb), as_text(cur), exact_amount(bal), exact_amount(bal_ac)]
			for a, fb, cur, bal, bal_ac in opening
		),
	}


def sle_component(company, start, end):
	sle = frappe.qb.DocType("Stock Ledger Entry")

	def grouped(*conditions):
		query = (
			frappe.qb.from_(sle)
			.select(
				sle.item_code, sle.warehouse, Sum(sle.actual_qty), Sum(sle.stock_value_difference), Count("*")
			)
			.where(sle.company == company)
			.where(sle.is_cancelled == 0)
			.groupby(sle.item_code, sle.warehouse)
		)
		for condition in conditions:
			query = query.where(condition)
		return sorted(
			[as_text(i), as_text(w), exact_amount(q), exact_amount(v), int(n)]
			for i, w, q, v, n in query.run()
		)

	return {
		"movement": grouped(sle.posting_date[start:end]),
		"opening": grouped(sle.posting_date < start),
	}


def ple_component(company, end):
	ple = frappe.qb.DocType("Payment Ledger Entry")
	group = [
		ple.account,
		ple.party_type,
		ple.party,
		ple.against_voucher_type,
		ple.against_voucher_no,
		ple.due_date,
	]
	rows = (
		frappe.qb.from_(ple)
		.select(*group, Sum(ple.amount), Sum(ple.amount_in_account_currency), Count("*"))
		.where(ple.company == company)
		.where(ple.delinked == 0)
		.where(ple.posting_date <= end)
		.groupby(*group)
		.run()
	)
	return sorted(
		[as_text(v) for v in row[:6]] + [exact_amount(row[6]), exact_amount(row[7]), int(row[8])]
		for row in rows
	)


def drafts_component(company, start, end):
	from erpcore.erp_core.monthly_close.checks.ledger import draft_rows

	return sorted(
		[
			r["doctype"],
			r["name"],
			exact_amount(r.get("amount")),
			as_text(r.get("workflow_state")),
			as_text(r["modified"]),
		]
		for r in draft_rows(company, start, end)
	)


def reposts_component(company, end):
	from erpcore.erp_core.monthly_close.checks.stock import pending_reposts

	return sorted([r["doctype"], r["name"], as_text(r.get("status"))] for r in pending_reposts(company, end))


def assets_component(company, end):
	schedule = frappe.qb.DocType("Depreciation Schedule")
	ads = frappe.qb.DocType("Asset Depreciation Schedule")
	rows = (
		frappe.qb.from_(schedule)
		.inner_join(ads)
		.on(schedule.parent == ads.name)
		.select(schedule.name, schedule.depreciation_amount)
		.where(ads.company == company)
		.where(ads.docstatus == 1)
		.where(ads.status == "Active")
		.where(schedule.schedule_date <= end)
		.where(schedule.journal_entry.isnull() | (schedule.journal_entry == ""))
		.run()
	)
	return sorted([as_text(n), exact_amount(a)] for n, a in rows)


def bank_component(close):
	return sorted(
		[as_text(v) for v in row]
		for row in frappe.get_all(
			BANK_CERT_DOCTYPE,
			filters={"monthly_close": close.name, "revision": close.revision},
			fields=["name", "bank_account", "status", "certified_hash", "statement_hash"],
			as_list=True,
		)
	)


def evidence_component(close):
	tasks = frappe.get_all(
		TASK_DOCTYPE,
		filters={"monthly_close": close.name, "revision": close.revision, "evidence": ["is", "set"]},
		fields=["name", "evidence", "evidence_hash"],
		as_list=True,
	)
	return sorted([as_text(v) for v in row] for row in tasks)


def compute(close) -> tuple[str, dict]:
	"""Return (overall hash, per-component hashes) for the close's company and month."""
	company, start, end = close.company, close.period_start, close.period_end
	components = {
		"gl": digest_of(gl_component(company, start, end)),
		"sle": digest_of(sle_component(company, start, end)),
		"ple": digest_of(ple_component(company, end)),
		"drafts": digest_of(drafts_component(company, start, end)),
		"reposts": digest_of(reposts_component(company, end)),
		"assets": digest_of(assets_component(company, end)),
		"bank": digest_of(bank_component(close)),
		"evidence": digest_of(evidence_component(close)),
		"policy": digest_of(
			[
				close.get("policy_hash") or close.policy_version,
				digest_of(close.template_snapshot or ""),
				close.revision,
			]
		),
	}
	return digest_of(components), components


FINANCIAL_COMPONENTS = ("gl", "sle", "ple", "drafts", "reposts", "assets")


def changed_components(old: dict | str | None, new: dict, only=None) -> list[str]:
	if isinstance(old, str):
		old = json.loads(old or "{}")
	old = old or {}
	keys = only or new.keys()
	return sorted(key for key in keys if old.get(key) != new.get(key))


def cached_digest(close) -> str:
	"""Dashboard-only: a recent fingerprint, recomputed at most every DASHBOARD_CACHE_SECONDS."""
	key = f"erpcore:mc_fingerprint:{close.name}:{close.revision}"
	cached = frappe.cache.get_value(key)
	if cached:
		return cached
	digest = compute(close)[0]
	frappe.cache.set_value(key, digest, expires_in_sec=DASHBOARD_CACHE_SECONDS)
	return digest
