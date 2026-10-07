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


@register_check(
	"stock_accounting",
	version=1,
	label="Stock and accounting consistency",
	default_severity=BLOCKER,
	description="Pending or failed Repost Item Valuation up to month end always blocks. Voucher-level differences between stock value and stock account GL come from ERPNext's Stock and Account Value Comparison report. Not applicable without perpetual inventory.",
	uses_tolerance=True,
)
def stock_accounting(ctx: CheckContext) -> Finding:
	import erpnext

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

	reposts = frappe.get_all(
		"Repost Item Valuation",
		filters={
			"company": ctx.company,
			"docstatus": 1,
			"posting_date": ["<=", ctx.period_end],
			"status": ["in", ["Queued", "In Progress", "Failed"]],
		},
		fields=["name", "status", "posting_date", "voucher_type", "voucher_no"],
		limit=ctx.sample_limit,
	)
	if reposts:
		return Finding(
			FINDING,
			_(
				"Stock reposting is not finished for dates up to month end. Valuation and GL may still change."
			),
			count=len(reposts),
			samples=bounded(reposts, ctx.sample_limit),
			route="repost-item-valuation",
			minimum_severity=BLOCKER,
		)

	from erpnext.stock.report.stock_and_account_value_comparison.stock_and_account_value_comparison import (
		execute,
	)

	_columns, rows = execute(
		frappe._dict(company=ctx.company, as_on_date=ctx.period_end, from_date=ctx.period_start)
	)
	differences = [
		{
			"voucher_type": row.get("voucher_type"),
			"voucher_no": row.get("voucher_no"),
			"stock_value": flt(row.get("stock_value"), ctx.precision),
			"account_value": flt(row.get("account_value"), ctx.precision),
			"difference": flt(row.get("difference_value"), ctx.precision),
		}
		for row in rows or []
	]
	if not differences:
		return Finding(PASSED, _("Stock value matches the stock accounts for every voucher in the month."))

	return Finding(
		FINDING,
		_("{0} vouchers in the month have stock value different from their stock account postings.").format(
			len(differences)
		),
		count=len(differences),
		amount=flt(sum(abs(d["difference"]) for d in differences), ctx.precision),
		samples=bounded(sorted(differences, key=lambda d: -abs(d["difference"])), ctx.sample_limit),
		route="query-report/Stock and Account Value Comparison",
	)
