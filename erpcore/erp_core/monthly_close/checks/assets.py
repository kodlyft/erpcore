# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Depreciation and deferred accounting due through month end. Nothing is posted by these checks."""

import frappe
from frappe import _
from frappe.query_builder.functions import Max
from frappe.utils import flt, getdate

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
		identities=rows,
		route="query-report/Fixed Asset Register",
	)


def deferred_items(ctx: CheckContext, invoice_doctype: str, enable_field: str, account_field: str):
	"""Every submitted deferred line that started by month end, with its last *posted* booking.

	Mirrors ERPNext's `deferred_revenue.get_booking_dates`, which takes the later of
	the last GL Entry on the deferred account for the line and the last Journal
	Entry referencing it, except that only submitted journals count here: native
	code also counts draft journals (`docstatus < 2`), which have posted nothing.
	"""
	item = frappe.qb.DocType(f"{invoice_doctype} Item")
	invoice = frappe.qb.DocType(invoice_doctype)
	lines = (
		frappe.qb.from_(item)
		.inner_join(invoice)
		.on(invoice.name == item.parent)
		.select(
			item.name,
			item.parent,
			item.service_start_date,
			item.service_end_date,
			item.service_stop_date,
			item[account_field].as_("deferred_account"),
			item.base_net_amount,
		)
		.where(invoice.company == ctx.company)
		.where(invoice.docstatus == 1)
		.where(item[enable_field] == 1)
		.where(item.service_start_date <= ctx.period_end)
		.run(as_dict=True)
	)
	if not lines:
		return []

	names = [line.name for line in lines]
	gle = frappe.qb.DocType("GL Entry")
	gl_booked = {
		(row.voucher_detail_no, row.account): row.booked_to
		for row in (
			frappe.qb.from_(gle)
			.select(gle.voucher_detail_no, gle.account, Max(gle.posting_date).as_("booked_to"))
			.where(gle.company == ctx.company)
			.where(gle.voucher_type == invoice_doctype)
			.where(gle.voucher_detail_no.isin(names))
			.where(gle.is_cancelled == 0)
			.where(gle.posting_date <= ctx.period_end)
			.groupby(gle.voucher_detail_no, gle.account)
			.run(as_dict=True)
		)
	}

	je = frappe.qb.DocType("Journal Entry")
	jea = frappe.qb.DocType("Journal Entry Account")
	journals = (
		frappe.qb.from_(je)
		.inner_join(jea)
		.on(jea.parent == je.name)
		.select(jea.reference_detail_no, jea.account, je.name, je.docstatus, je.posting_date)
		.where(je.docstatus < 2)
		.where(jea.reference_type == invoice_doctype)
		.where(jea.reference_detail_no.isin(names))
		.run(as_dict=True)
	)

	for line in lines:
		line.gl_booked_to = gl_booked.get((line.name, line.deferred_account))
		posted = [
			j.posting_date
			for j in journals
			if j.reference_detail_no == line.name
			and j.account == line.deferred_account
			and j.docstatus == 1
			and getdate(j.posting_date) <= getdate(ctx.period_end)
		]
		line.je_booked_to = max(posted) if posted else None
		drafts = sorted({j.name for j in journals if j.reference_detail_no == line.name and j.docstatus == 0})
		line.draft_journals = ",".join(drafts) or None
	return lines


@register_check(
	"deferred_due",
	version=2,
	label="Deferred revenue and expense due through month end",
	default_severity=WARNING,
	description="Every submitted deferred revenue/expense line, including older lines whose service ended before this month, that is not booked through month end (or its service end/stop date, if earlier). Only submitted bookings count; a draft journal is reported, not treated as booked. Run Process Deferred Accounting to book them.",
)
def deferred_due(ctx: CheckContext) -> Finding:
	pending, examined = [], 0
	for invoice_doctype, enable_field, account_field in (
		("Sales Invoice", "enable_deferred_revenue", "deferred_revenue_account"),
		("Purchase Invoice", "enable_deferred_expense", "deferred_expense_account"),
	):
		for row in deferred_items(ctx, invoice_doctype, enable_field, account_field):
			examined += 1
			service_end = getdate(row.service_stop_date or row.service_end_date)
			required_to = min(getdate(ctx.period_end), service_end)
			booked = [getdate(d) for d in (row.gl_booked_to, row.je_booked_to) if d]
			booked_to = max(booked) if booked else None
			if booked_to and booked_to >= required_to:
				continue
			pending.append(
				{
					"invoice_type": invoice_doctype,
					"invoice": row.parent,
					"item_row": row.name,
					"service_end": service_end,
					"booked_to": booked_to,
					"required_to": required_to,
					"draft_journals": row.draft_journals,
				}
			)

	if not examined:
		return Finding(NOT_APPLICABLE, _("No deferred revenue or expense lines started by month end."))

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

	in_drafts = [p for p in pending if p["draft_journals"]]
	return Finding(
		FINDING,
		_("{0} of {1} deferred lines still need booking through month end.").format(len(pending), examined)
		+ (
			" "
			+ _("{0} of them are booked only in draft journals, which post nothing.").format(len(in_drafts))
			if in_drafts
			else ""
		)
		+ evidence,
		count=len(pending),
		samples=bounded(pending, ctx.sample_limit),
		identities=pending,
		route="process-deferred-accounting/new",
	)
