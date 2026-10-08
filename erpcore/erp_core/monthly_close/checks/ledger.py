# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""General ledger checks: drafts, trial balance integrity, suspense balances, FX exposure."""

import frappe
from frappe import _
from frappe.query_builder.functions import Abs, Count, Sum
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

DATE_FIELDS = {
	"Asset": "available_for_use_date",
	"Asset Repair": "completion_date",
	"Period Closing Voucher": "period_end_date",
}
NOT_VOUCHERS = ("Bank Clearance",)
AMOUNT_FIELDS = (
	"base_grand_total",
	"total_debit",
	"base_paid_amount",
	"difference_amount",
	"total_taxes_and_charges",
	"total_outgoing_value",
	"total_asset_cost",
	"gross_purchase_amount",
	"repair_cost",
)
BASE_DRAFT_DOCTYPES = (
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


def draft_adapters() -> list[frappe._dict]:
	"""Submittable voucher doctypes with a company and a date, and how to read them."""
	candidates = [{"doctype": dt} for dt in BASE_DRAFT_DOCTYPES]
	candidates += [{"doctype": dt} for dt in frappe.get_hooks("period_closing_doctypes")]
	for entry in frappe.get_hooks("erpcore_monthly_close_draft_vouchers"):
		candidates.append(frappe.parse_json(entry) if isinstance(entry, str) else entry)

	adapters, seen = [], set()
	for entry in candidates:
		doctype = entry.get("doctype")
		if not doctype or doctype in seen or doctype in NOT_VOUCHERS:
			continue
		seen.add(doctype)
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		date_field = entry.get("date_field") or DATE_FIELDS.get(doctype, "posting_date")
		if not meta.is_submittable or not meta.has_field("company") or not meta.has_field(date_field):
			continue
		amount_field = entry.get("amount_field") or next(
			(f for f in AMOUNT_FIELDS if meta.has_field(f)), None
		)
		workflow_field = meta.get_workflow() and frappe.db.get_value(
			"Workflow", meta.get_workflow(), "workflow_state_field"
		)
		adapters.append(
			frappe._dict(
				doctype=doctype,
				date_field=date_field,
				amount_field=amount_field,
				workflow_field=workflow_field if workflow_field and meta.has_field(workflow_field) else None,
			)
		)
	return adapters


def draft_rows(company: str, start, end) -> list[dict]:
	"""Every draft voucher dated in the month, with its workflow state and amount."""
	rows = []
	for adapter in draft_adapters():
		table = frappe.qb.DocType(adapter.doctype)
		fields = [table.name, table.owner, table.modified, table[adapter.date_field].as_("date")]
		if adapter.amount_field:
			fields.append(table[adapter.amount_field].as_("amount"))
		if adapter.workflow_field:
			fields.append(table[adapter.workflow_field].as_("workflow_state"))
		for row in (
			frappe.qb.from_(table)
			.select(*fields)
			.where(table.company == company)
			.where(table.docstatus == 0)
			.where(table[adapter.date_field][start:end])
			.orderby(table[adapter.date_field], table.name)
			.run(as_dict=True)
		):
			rows.append({"doctype": adapter.doctype, **row})
	return rows


@register_check(
	"draft_vouchers",
	version=2,
	label="Draft accounting vouchers in the month",
	default_severity=BLOCKER,
	description="Drafts (in any unapproved workflow state) dated in the month, across every registered voucher type. They must be submitted, re-dated, deleted or covered by an approved exception. They are never submitted automatically.",
)
def draft_vouchers(ctx: CheckContext) -> Finding:
	rows = draft_rows(ctx.company, ctx.period_start, ctx.period_end)
	if not rows:
		return Finding(PASSED, _("No draft vouchers are dated in the month."))

	by_type: dict[str, int] = {}
	for row in rows:
		by_type[row["doctype"]] = by_type.get(row["doctype"], 0) + 1

	identities = [
		{
			"doctype": r["doctype"],
			"name": r["name"],
			"date": r["date"],
			"amount": flt(r.get("amount"), ctx.precision),
			"workflow_state": r.get("workflow_state"),
			"modified": r["modified"],
		}
		for r in rows
	]
	return Finding(
		FINDING,
		_("{0} draft vouchers are dated in the month: {1}.").format(
			len(rows), ", ".join(f"{_(dt)} {n}" for dt, n in by_type.items())
		),
		count=len(rows),
		amount=flt(sum(flt(r.get("amount")) for r in rows), ctx.precision),
		samples=bounded(
			[{k: v for k, v in r.items() if k != "modified"} for r in identities], ctx.sample_limit
		),
		identities=identities,
		route="query-report/Monthly Close Exceptions",
	)


@register_check(
	"trial_balance",
	version=2,
	label="Trial balance integrity",
	default_severity=BLOCKER,
	description="Debits equal credits for the month's movement and for balances brought forward, per finance book, at the company currency's precision. Lists vouchers that do not balance.",
)
def trial_balance(ctx: CheckContext) -> Finding:
	gle = frappe.qb.DocType("GL Entry")

	def totals(*conditions):
		query = (
			frappe.qb.from_(gle)
			.select(gle.finance_book, Sum(gle.debit), Sum(gle.credit))
			.where(gle.company == ctx.company)
			.where(gle.is_cancelled == 0)
			.groupby(gle.finance_book)
		)
		for condition in conditions:
			query = query.where(condition)
		return query.run()

	problems = []
	for label, rows in (
		(_("Brought forward"), totals(gle.posting_date < ctx.period_start)),
		(_("Month movement"), totals(gle.posting_date[ctx.period_start : ctx.period_end])),
	):
		for finance_book, debit, credit in rows:
			difference = flt(flt(debit) - flt(credit), ctx.precision)
			if difference:
				problems.append(
					{
						"scope": label,
						"finance_book": finance_book or _("(no finance book)"),
						"debit": flt(debit, ctx.precision),
						"credit": flt(credit, ctx.precision),
						"difference": difference,
					}
				)

	unbalanced = (
		frappe.qb.from_(gle)
		.select(gle.voucher_type, gle.voucher_no, (Sum(gle.debit) - Sum(gle.credit)).as_("difference"))
		.where(gle.company == ctx.company)
		.where(gle.is_cancelled == 0)
		.where(gle.posting_date[ctx.period_start : ctx.period_end])
		.groupby(gle.voucher_type, gle.voucher_no, gle.finance_book)
		.having(Abs(Sum(gle.debit) - Sum(gle.credit)) >= 0.5 / (10**ctx.precision))
		.run(as_dict=True)
	)

	if not problems and not unbalanced:
		return Finding(PASSED, _("Debits equal credits for the month and for balances brought forward."))

	return Finding(
		FINDING,
		_("The ledger does not balance at {0} decimal places.").format(ctx.precision),
		count=len(problems) + len(unbalanced),
		amount=sum(abs(p["difference"]) for p in problems),
		samples=bounded(problems + [dict(r) for r in unbalanced], ctx.sample_limit),
		identities=problems + [dict(r) for r in unbalanced],
		route="query-report/Trial Balance",
		minimum_severity=BLOCKER,
		waivable=False,
	)


def account_balances(company, accounts, end, in_account_currency=False):
	gle = frappe.qb.DocType("GL Entry")
	debit, credit = (
		(gle.debit_in_account_currency, gle.credit_in_account_currency)
		if in_account_currency
		else (gle.debit, gle.credit)
	)
	return (
		frappe.qb.from_(gle)
		.select(gle.account, (Sum(debit) - Sum(credit)).as_("balance"))
		.where(gle.company == company)
		.where(gle.is_cancelled == 0)
		.where(gle.account.isin(accounts))
		.where(gle.posting_date <= end)
		.groupby(gle.account)
		.run(as_dict=True)
	)


@register_check(
	"suspense_accounts",
	version=1,
	label="Suspense and temporary account balances",
	default_severity=WARNING,
	description="Balances left on the accounts listed in the company's close policy. Accounts are picked explicitly per company; names are never guessed.",
	uses_tolerance=True,
)
def suspense_accounts(ctx: CheckContext) -> Finding:
	rows = [row for row in (ctx.policy.get("suspense_accounts") or []) if row.account]
	if not rows:
		return Finding(NOT_APPLICABLE, _("No suspense or temporary accounts are listed in the close policy."))

	limits = {row.account: flt(row.tolerance) for row in rows}
	balances = account_balances(ctx.company, list(limits), ctx.period_end)

	over = []
	for row in balances:
		balance = flt(row.balance, ctx.precision)
		limit = limits.get(row.account) or flt(ctx.tolerance)
		if abs(balance) > abs(limit):
			over.append({"account": row.account, "balance": balance, "tolerance": limit})

	if not over:
		return Finding(PASSED, _("All listed suspense accounts are within tolerance."))

	return Finding(
		FINDING,
		_("{0} suspense accounts carry balances above tolerance.").format(len(over)),
		count=len(over),
		amount=sum(abs(r["balance"]) for r in over),
		samples=bounded(over, ctx.sample_limit),
		identities=over,
		route="query-report/General Ledger",
		# per-account tolerances have been applied already
		tolerance_applies=False,
	)


@register_check(
	"fx_revaluation",
	version=2,
	label="Foreign currency exposure and revaluation",
	default_severity=WARNING,
	description="Balance sheet accounts in a foreign currency with an open balance at month end. Passes only when a submitted Exchange Rate Revaluation dated on the last day of the month includes every exposed account and its gain/loss journal is posted. A revaluation elsewhere in the month is evidence, not proof. Nothing is posted automatically.",
)
def fx_revaluation(ctx: CheckContext) -> Finding:
	accounts = frappe.get_all(
		"Account",
		filters={
			"company": ctx.company,
			"is_group": 0,
			"account_currency": ["not in", ["", ctx.currency]],
			"root_type": ["in", ["Asset", "Liability"]],
		},
		fields=["name", "account_currency"],
	)
	if not accounts:
		return Finding(NOT_APPLICABLE, _("The company has no foreign-currency balance sheet accounts."))

	currencies = {a.name: a.account_currency for a in accounts}
	exposed = [
		{
			"account": row.account,
			"currency": currencies.get(row.account),
			"balance_in_account_currency": flt(row.balance, ctx.precision),
		}
		for row in account_balances(ctx.company, list(currencies), ctx.period_end, in_account_currency=True)
		if flt(row.balance, ctx.precision)
	]
	if not exposed:
		return Finding(NOT_APPLICABLE, _("No foreign-currency account has a balance at month end."))

	in_month = frappe.get_all(
		"Exchange Rate Revaluation",
		filters={
			"company": ctx.company,
			"docstatus": 1,
			"posting_date": ["between", [ctx.period_start, ctx.period_end]],
		},
		fields=["name", "posting_date", "total_gain_loss"],
		order_by="posting_date desc",
	)
	at_month_end = [r for r in in_month if getdate(r.posting_date) == getdate(ctx.period_end)]

	covered = set()
	unposted = []
	for err in at_month_end:
		covered.update(
			frappe.get_all("Exchange Rate Revaluation Account", filters={"parent": err.name}, pluck="account")
		)
		if flt(err.total_gain_loss, ctx.precision) and not revaluation_journal_posted(err.name):
			unposted.append(err.name)

	missing = [row for row in exposed if row["account"] not in covered]
	problems = []
	if not at_month_end:
		problems.append(
			_("No Exchange Rate Revaluation is dated {0} (month end).").format(ctx.period_end)
			+ (
				" "
				+ _("Revaluations elsewhere in the month ({0}) do not value month-end balances.").format(
					", ".join(r.name for r in in_month)
				)
				if in_month
				else ""
			)
		)
	elif missing:
		problems.append(_("{0} exposed accounts are not in the month-end revaluation.").format(len(missing)))
	if unposted:
		problems.append(_("Gain/loss journals of {0} are not submitted.").format(", ".join(unposted)))

	if not problems:
		return Finding(
			PASSED,
			_("{0} foreign-currency accounts were revalued at month end by {1}.").format(
				len(exposed), ", ".join(r.name for r in at_month_end)
			),
			count=len(exposed),
			samples=bounded(exposed, ctx.sample_limit),
		)

	identities = [{"not_revalued": r} for r in (missing if at_month_end else exposed)] + [
		{"unposted_revaluation": n} for n in unposted
	]
	return Finding(
		FINDING,
		" ".join(problems)
		+ " "
		+ _("Review whether a revaluation is required under the company's policy, or certify the exposure."),
		count=len(identities),
		samples=bounded(missing or exposed, ctx.sample_limit),
		identities=identities,
		route="exchange-rate-revaluation/new",
	)


def revaluation_journal_posted(revaluation: str) -> bool:
	jea = frappe.qb.DocType("Journal Entry Account")
	return bool(
		frappe.qb.from_(jea)
		.select(jea.parent)
		.where(jea.reference_type == "Exchange Rate Revaluation")
		.where(jea.reference_name == revaluation)
		.where(jea.docstatus == 1)
		.limit(1)
		.run()
	)
