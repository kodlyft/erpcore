# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Depreciation and deferred accounting due through month end. Nothing is posted by these checks."""

import frappe
from frappe import _
from frappe.utils import flt

from erpcore.erp_core.monthly_close.checks.registry import (
	BLOCKER,
	FINDING,
	NOT_APPLICABLE,
	PASSED,
	WARNING,
	CheckContext,
	Finding,
	bounded,
	register_check,
)

# Assets in these states legitimately have unposted schedule rows.
EXCLUDED_ASSET_STATUSES = ("Draft", "Scrapped", "Sold", "Cancelled", "Capitalized", "Decapitalized")


@register_check(
	"depreciation_due",
	version=1,
	label="Depreciation due through month end",
	default_severity=BLOCKER,
	description="Active depreciation schedule rows dated on or before month end that have no depreciation Journal Entry, per finance book. Scrapped, sold and capitalised assets are excluded. Post through ERPNext's asset tools.",
)
def depreciation_due(ctx: CheckContext) -> Finding:
	if not frappe.db.exists("Asset", {"company": ctx.company, "docstatus": 1}):
		return Finding(NOT_APPLICABLE, _("The company has no submitted assets."))

	schedule = frappe.qb.DocType("Depreciation Schedule")
	ads = frappe.qb.DocType("Asset Depreciation Schedule")
	asset = frappe.qb.DocType("Asset")

	rows = (
		frappe.qb.from_(schedule)
		.inner_join(ads)
		.on(schedule.parent == ads.name)
		.inner_join(asset)
		.on(asset.name == ads.asset)
		.select(
			ads.asset,
			ads.finance_book,
			schedule.schedule_date,
			schedule.depreciation_amount,
			asset.status.as_("asset_status"),
		)
		.where(schedule.parenttype == "Asset Depreciation Schedule")
		.where(ads.company == ctx.company)
		.where(ads.docstatus == 1)
		.where(ads.status == "Active")
		.where(asset.docstatus == 1)
		.where(asset.status.notin(EXCLUDED_ASSET_STATUSES))
		.where(schedule.schedule_date <= ctx.period_end)
		.where(schedule.journal_entry.isnull() | (schedule.journal_entry == ""))
		.orderby(schedule.schedule_date)
		.run(as_dict=True)
	)

	if not rows:
		return Finding(PASSED, _("All depreciation due through month end has been posted."))

	books = sorted({row.finance_book or _("(default book)") for row in rows})
	return Finding(
		FINDING,
		_("{0} depreciation rows due by month end are not posted (finance books: {1}).").format(
			len(rows), ", ".join(books)
		),
		count=len(rows),
		amount=flt(sum(flt(r.depreciation_amount) for r in rows), ctx.precision),
		samples=bounded(rows, ctx.sample_limit),
		route="query-report/Fixed Asset Register",
	)


def _deferred_items(ctx: CheckContext, invoice_doctype: str, enable_field: str, account_field: str):
	item = frappe.qb.DocType(f"{invoice_doctype} Item")
	invoice = frappe.qb.DocType(invoice_doctype)
	return (
		frappe.qb.from_(item)
		.inner_join(invoice)
		.on(invoice.name == item.parent)
		.select(
			item.name,
			item.parent,
			item.service_start_date,
			item.service_end_date,
			item.service_stop_date,
			item[account_field],
			item.base_net_amount,
		)
		.where(invoice.company == ctx.company)
		.where(invoice.docstatus == 1)
		.where(item[enable_field] == 1)
		.where(item.service_start_date <= ctx.period_end)
		.where(item.service_end_date >= ctx.period_start)
		.run(as_dict=True)
	)


@register_check(
	"deferred_due",
	version=1,
	label="Deferred revenue and expense due through month end",
	default_severity=WARNING,
	description="Invoice lines with deferred revenue/expense whose booking through month end is still pending, judged with ERPNext's own booking-date logic. Run Process Deferred Accounting to book them.",
)
def deferred_due(ctx: CheckContext) -> Finding:
	from erpnext.accounts.deferred_revenue import get_booking_dates

	pending, examined = [], 0
	for invoice_doctype, enable_field, account_field in (
		("Sales Invoice", "enable_deferred_revenue", "deferred_revenue_account"),
		("Purchase Invoice", "enable_deferred_expense", "deferred_expense_account"),
	):
		for row in _deferred_items(ctx, invoice_doctype, enable_field, account_field):
			examined += 1
			invoice = frappe._dict(doctype=invoice_doctype, name=row.parent, company=ctx.company)
			start, end, _last = get_booking_dates(invoice, row, posting_date=ctx.period_end)
			if start and end:
				pending.append(
					{
						"invoice_type": invoice_doctype,
						"invoice": row.parent,
						"item_row": row.name,
						"unbooked_from": start,
						"unbooked_to": end,
					}
				)

	if not examined:
		return Finding(NOT_APPLICABLE, _("No deferred revenue or expense lines overlap the month."))

	runs = frappe.get_all(
		"Process Deferred Accounting",
		filters={"company": ctx.company, "docstatus": 1, "end_date": [">=", ctx.period_end]},
		pluck="name",
		limit=5,
	)
	evidence = " " + _("Process Deferred Accounting runs covering month end: {0}.").format(
		", ".join(runs) or _("none")
	)

	if not pending:
		return Finding(
			PASSED, _("{0} deferred lines are booked through month end.").format(examined) + evidence
		)

	return Finding(
		FINDING,
		_("{0} of {1} deferred lines still need booking through month end.").format(len(pending), examined)
		+ evidence,
		count=len(pending),
		samples=bounded(pending, ctx.sample_limit),
		route="process-deferred-accounting/new",
	)
