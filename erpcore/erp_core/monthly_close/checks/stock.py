# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Stock ledger vs accounts, for perpetual-inventory companies."""

import frappe
from frappe import _
from frappe.utils import flt

from erpcore.erp_core.monthly_close.checks.registry import (
	BLOCKER,
	FINDING,
	NOT_APPLICABLE,
	PASSED,
	CheckContext,
	Finding,
	bounded,
	register_check,
)

UNFINISHED_RIV = ("Queued", "In Progress", "Failed")
UNFINISHED_RAL = ("Queued", "In Progress", "Partially Reposted", "Failed")
UNFINISHED_RPL = ("Queued", "Failed")


def pending_reposts(company: str, end) -> list[dict]:
	"""Submitted reposts that have not finished and could still rewrite ledgers up to `end`.

	Repost Accounting Ledger has no date of its own; every unfinished one of the
	company is included (fail closed).
	"""
	rows = [
		{"doctype": "Repost Item Valuation", **row}
		for row in frappe.get_all(
			"Repost Item Valuation",
			filters={
				"company": company,
				"docstatus": 1,
				"posting_date": ["<=", end],
				"status": ["in", UNFINISHED_RIV],
			},
			fields=["name", "status", "posting_date", "voucher_type", "voucher_no"],
			order_by="posting_date asc, name asc",
		)
	]
	rows += [
		{"doctype": "Repost Accounting Ledger", **row}
		for row in frappe.get_all(
			"Repost Accounting Ledger",
			filters={"company": company, "docstatus": 1, "status": ["in", UNFINISHED_RAL]},
			fields=["name", "status"],
			order_by="name asc",
		)
	]
	rows += [
		{"doctype": "Repost Payment Ledger", **row}
		for row in frappe.get_all(
			"Repost Payment Ledger",
			filters={
				"company": company,
				"docstatus": 1,
				"posting_date": ["<=", end],
				"repost_status": ["in", UNFINISHED_RPL],
			},
			fields=["name", "repost_status as status", "posting_date"],
			order_by="posting_date asc, name asc",
		)
	]
	return rows


def cumulative_differences(ctx: CheckContext) -> list[dict]:
	"""Stock value vs stock account balance as at month end, per stock account.

	The voucher-level comparison only covers the month's vouchers; an older
	mismatch still sits in the month-end balance, and this finds it.
	"""
	from erpnext.accounts.utils import get_stock_and_account_balance

	differences = []
	for account in frappe.get_all(
		"Account",
		filters={"company": ctx.company, "account_type": "Stock", "is_group": 0},
		pluck="name",
		order_by="name",
	):
		account_balance, stock_value = get_stock_and_account_balance(account, ctx.period_end, ctx.company)[:2]
		difference = flt(flt(stock_value) - flt(account_balance), ctx.precision)
		if difference:
			differences.append(
				{
					"scope": "As at month end",
					"account": account,
					"stock_value": flt(stock_value, ctx.precision),
					"account_value": flt(account_balance, ctx.precision),
					"difference": difference,
				}
			)
	return differences


@register_check(
	"stock_accounting",
	version=2,
	label="Stock and accounting consistency",
	default_severity=BLOCKER,
	description="Unfinished or failed Repost Item Valuation, Repost Accounting Ledger or Repost Payment Ledger up to month end always block and cannot be waived. Then: stock value vs stock account balance as at month end per stock account (catches older mismatches carried in), and voucher-level differences for the month from ERPNext's Stock and Account Value Comparison. Not applicable without perpetual inventory.",
	uses_tolerance=True,
)
def stock_accounting(ctx: CheckContext) -> Finding:
	import erpnext

	reposts = pending_reposts(ctx.company, ctx.period_end)
	if reposts:
		return Finding(
			FINDING,
			_(
				"Reposting is not finished for dates up to month end ({0}). Stock valuation, GL and Payment Ledger may still change."
			).format(", ".join(sorted({r["doctype"] for r in reposts}))),
			count=len(reposts),
			samples=bounded(reposts, ctx.sample_limit),
			identities=reposts,
			route="repost-item-valuation",
			minimum_severity=BLOCKER,
			tolerance_applies=False,
			waivable=False,
		)

	has_stock = frappe.db.exists("Stock Ledger Entry", {"company": ctx.company, "is_cancelled": 0})
	if not erpnext.is_perpetual_inventory_enabled(ctx.company):
		reason = (
			_("Perpetual inventory is off, so stock does not post to the GL.")
			if has_stock
			else _("The company keeps no stock.")
		)
		return Finding(NOT_APPLICABLE, reason)

	if not has_stock:
		return Finding(NOT_APPLICABLE, _("The company has no stock ledger entries."))

	from erpnext.stock.report.stock_and_account_value_comparison.stock_and_account_value_comparison import (
		execute,
	)

	rows = execute(frappe._dict(company=ctx.company, as_on_date=ctx.period_end, from_date=ctx.period_start))[
		1
	]
	differences = cumulative_differences(ctx) + [
		{
			"scope": "Voucher in month",
			"voucher_type": row.get("voucher_type"),
			"voucher_no": row.get("voucher_no"),
			"stock_value": flt(row.get("stock_value"), ctx.precision),
			"account_value": flt(row.get("account_value"), ctx.precision),
			"difference": flt(row.get("difference_value"), ctx.precision),
		}
		for row in rows or []
	]
	if not differences:
		return Finding(
			PASSED,
			_("Stock value matches the stock accounts at month end and for every voucher in the month."),
		)

	cumulative = [d for d in differences if d["scope"] == "As at month end"]
	return Finding(
		FINDING,
		_(
			"{0} stock accounts differ from stock value at month end; {1} vouchers in the month differ."
		).format(len(cumulative), len(differences) - len(cumulative)),
		count=len(differences),
		amount=flt(sum(abs(d["difference"]) for d in (cumulative or differences)), ctx.precision),
		samples=bounded(sorted(differences, key=lambda d: -abs(d["difference"])), ctx.sample_limit),
		identities=differences,
		route="query-report/Stock and Account Value Comparison",
	)
