# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""General ledger checks: drafts, trial balance integrity, suspense balances, FX exposure."""

import frappe
from frappe import _
from frappe.query_builder.functions import Abs, Count, Sum
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

# (doctype, amount field shown to the reviewer). None where no single amount is meaningful.
DRAFT_VOUCHERS = (
	("Sales Invoice", "base_grand_total"),
	("Purchase Invoice", "base_grand_total"),
	("Journal Entry", "total_debit"),
	("Payment Entry", "base_paid_amount"),
	("Stock Entry", None),
	("Stock Reconciliation", "difference_amount"),
	("Delivery Note", "base_grand_total"),
	("Purchase Receipt", "base_grand_total"),
	("Landed Cost Voucher", "total_taxes_and_charges"),
)


@register_check(
	"draft_vouchers",
	version=1,
	label="Draft accounting vouchers in the month",
	default_severity=BLOCKER,
	description="Drafts dated in the month must be submitted, re-dated, deleted or covered by an approved exception. They are never submitted automatically.",
)
def draft_vouchers(ctx: CheckContext) -> Finding:
	total_count, total_amount, samples, by_type = 0, 0.0, [], {}

	for doctype, amount_field in DRAFT_VOUCHERS:
		if not frappe.db.exists("DocType", doctype):
			continue

		table = frappe.qb.DocType(doctype)
		fields = [table.name, table.owner, table.posting_date]
		if amount_field:
			fields.append(table[amount_field].as_("amount"))

		query = (
			frappe.qb.from_(table)
			.select(*fields)
			.where(table.company == ctx.company)
			.where(table.docstatus == 0)
			.where(table.posting_date[ctx.period_start : ctx.period_end])
			.orderby(table.posting_date)
		)
		count = (
			frappe.qb.from_(table)
			.select(Count("*"))
			.where(table.company == ctx.company)
			.where(table.docstatus == 0)
			.where(table.posting_date[ctx.period_start : ctx.period_end])
			.run()[0][0]
		)
		if not count:
			continue

		rows = query.limit(ctx.sample_limit).run(as_dict=True)
		by_type[doctype] = count
		total_count += count
		if amount_field:
			total_amount += flt(
				frappe.qb.from_(table)
				.select(Sum(table[amount_field]))
				.where(table.company == ctx.company)
				.where(table.docstatus == 0)
				.where(table.posting_date[ctx.period_start : ctx.period_end])
				.run()[0][0]
			)

		for row in rows:
			if len(samples) < ctx.sample_limit:
				samples.append({"doctype": doctype, **row})

	if not total_count:
		return Finding(PASSED, _("No draft vouchers are dated in the month."))

	return Finding(
		FINDING,
		_("{0} draft vouchers are dated in the month: {1}.").format(
			total_count, ", ".join(f"{_(dt)} {n}" for dt, n in by_type.items())
		),
		count=total_count,
		amount=flt(total_amount, ctx.precision),
		samples=samples,
		route="query-report/Monthly Close Exceptions",
	)


@register_check(
	"trial_balance",
	version=1,
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
		.limit(ctx.sample_limit)
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
		route="query-report/Trial Balance",
		minimum_severity=BLOCKER,
	)


def _balance(company, accounts, end, in_account_currency=False):
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
	balances = _balance(ctx.company, list(limits), ctx.period_end)

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
		route="query-report/General Ledger",
		# per-account tolerances have been applied already
		tolerance_applies=False,
	)


@register_check(
	"fx_revaluation",
	version=1,
	label="Foreign currency exposure and revaluation",
	default_severity=WARNING,
	description="Balance sheet accounts in a foreign currency with an open balance at month end, and whether an Exchange Rate Revaluation was submitted for the month. Nothing is posted automatically.",
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
		pluck="name",
	)
	if not accounts:
		return Finding(NOT_APPLICABLE, _("The company has no foreign-currency balance sheet accounts."))

	exposed = [
		{"account": row.account, "balance_in_account_currency": flt(row.balance, ctx.precision)}
		for row in _balance(ctx.company, accounts, ctx.period_end, in_account_currency=True)
		if flt(row.balance, ctx.precision)
	]
	if not exposed:
		return Finding(NOT_APPLICABLE, _("No foreign-currency account has a balance at month end."))

	revalued = frappe.get_all(
		"Exchange Rate Revaluation",
		filters={
			"company": ctx.company,
			"docstatus": 1,
			"posting_date": ["between", [ctx.period_start, ctx.period_end]],
		},
		pluck="name",
	)
	if revalued:
		return Finding(
			PASSED,
			_("{0} foreign-currency accounts are open; revaluation {1} was submitted for the month.").format(
				len(exposed), ", ".join(revalued)
			),
			count=len(exposed),
			samples=bounded(exposed, ctx.sample_limit),
		)

	return Finding(
		FINDING,
		_(
			"{0} foreign-currency accounts carry balances and no Exchange Rate Revaluation was submitted in the month. Review whether one is required under the company's policy."
		).format(len(exposed)),
		count=len(exposed),
		samples=bounded(exposed, ctx.sample_limit),
		route="exchange-rate-revaluation/new",
	)
