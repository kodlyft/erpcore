# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Receivables/payables, bank certification and erpcore cheque register checks."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpcore.erp_core.cheque_constants import (
	LEAF_BOUNCED,
	LEAF_CLEARED,
	LEAF_ISSUED,
	LEAF_RESERVED,
	LEAF_STOPPED,
	VOUCHER_DOCTYPES,
)
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
from erpcore.erp_core.monthly_close.constants import BANK_CERT_DOCTYPE


def run_native_report(module_path: str, filters: dict):
	"""Run an installed ERPNext report's `execute` so its accounting semantics are reused, not copied."""
	execute = frappe.get_attr(f"{module_path}.execute")
	result = execute(frappe._dict(filters))
	return result[0], result[1] or []


@register_check(
	"ar_ap_ledger_integrity",
	version=1,
	label="Receivable/payable ledger integrity",
	default_severity=BLOCKER,
	description="Vouchers of the month whose General Ledger and Payment Ledger disagree on receivable/payable accounts (ERPNext's General and Payment Ledger Comparison). This is a ledger-integrity failure, unlike ordinary unpaid balances.",
)
def ar_ap_ledger_integrity(ctx: CheckContext) -> Finding:
	mismatches = run_native_report(
		"erpnext.accounts.report.general_and_payment_ledger_comparison.general_and_payment_ledger_comparison",
		{"company": ctx.company, "period_start_date": ctx.period_start, "period_end_date": ctx.period_end},
	)[1]
	if not mismatches:
		return Finding(PASSED, _("General Ledger and Payment Ledger agree for every voucher of the month."))

	rows = [dict(row) for row in mismatches]
	return Finding(
		FINDING,
		_("{0} vouchers differ between General Ledger and Payment Ledger.").format(len(rows)),
		count=len(rows),
		amount=flt(
			sum(abs(flt(r.get("gl_balance")) - flt(r.get("pl_balance"))) for r in rows), ctx.precision
		),
		samples=bounded(rows, ctx.sample_limit),
		identities=rows,
		route="query-report/General and Payment Ledger Comparison",
	)


@register_check(
	"ar_ap_review",
	version=2,
	label="Receivables and payables review",
	default_severity=WARNING,
	description="Ageing over 120 days and unallocated advances at month end, from ERPNext's receivable/payable summaries with month-end filters. Unpaid invoices are normal and are review items, not errors. Ledger mismatches are a separate check.",
)
def ar_ap_review(ctx: CheckContext) -> Finding:
	identities, notes, amount = [], [], 0.0

	summaries = (
		("Receivable", "erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary"),
		("Payable", "erpnext.accounts.report.accounts_payable_summary.accounts_payable_summary"),
	)
	for account_type, module_path in summaries:
		rows = run_native_report(module_path, ageing_filters(ctx))[1]
		rows = [frappe._dict(row) for row in rows if isinstance(row, dict) and row.get("party")]
		outstanding = sum(flt(row.outstanding) for row in rows)
		oldest = [row for row in rows if flt(row.get("range5"))]
		advances = [row for row in rows if flt(row.get("advance"))]

		notes.append(
			_(
				"{0}: {1} parties, outstanding {2}; {3} with balances over 120 days; {4} with unallocated advances."
			).format(
				_(account_type),
				len(rows),
				frappe.format(
					flt(outstanding, ctx.precision), {"fieldtype": "Currency", "options": ctx.currency}
				),
				len(oldest),
				len(advances),
			)
		)
		amount += sum(flt(row.get("range5")) for row in oldest)
		for row in sorted(oldest, key=lambda r: -flt(r.get("range5"))):
			identities.append(
				{
					"type": f"{account_type} over 120 days",
					"party_type": row.get("party_type"),
					"party": row.party,
					"amount": flt(row.get("range5"), ctx.precision),
				}
			)
		for row in advances:
			identities.append(
				{
					"type": f"{account_type} unallocated advance",
					"party_type": row.get("party_type"),
					"party": row.party,
					"amount": flt(row.advance, ctx.precision),
				}
			)

	return Finding(
		FINDING if identities else PASSED,
		" ".join(notes),
		count=len(identities),
		amount=flt(amount, ctx.precision),
		samples=bounded(identities, ctx.sample_limit),
		identities=identities,
		route="query-report/Accounts Receivable Summary",
	)


def ageing_filters(ctx) -> dict:
	"""The month-end filters used by the AR/AP check, the fingerprint and the packet snapshots."""
	return {
		"company": ctx.company,
		"report_date": ctx.period_end,
		"ageing_based_on": "Due Date",
		"age_as_on": "Report Date",
		"range": "30, 60, 90, 120",
	}


def company_bank_accounts(company: str) -> list[frappe._dict]:
	return frappe.get_all(
		"Bank Account",
		filters={"company": company, "is_company_account": 1, "disabled": 0, "account": ["is", "set"]},
		fields=["name", "account"],
		order_by="name",
	)


@register_check(
	"bank_reconciliation",
	version=2,
	label="Bank reconciliation certification",
	default_severity=BLOCKER,
	description="Each company bank account needs a certified workpaper for this revision: statement dated at month end (or bridged with an explanation), the statement file intact, ledger balance unchanged since certification, and any difference explained. Amounts are compared in each bank account's own currency; the policy tolerance applies to company-currency accounts only. Outstanding cheques are normal; missing evidence is not.",
	uses_tolerance=True,
)
def bank_reconciliation(ctx: CheckContext) -> Finding:
	from erpnext.accounts.utils import get_balance_on

	from erpcore.erp_core.doctype.monthly_close_bank_certification.monthly_close_bank_certification import (
		certified_content_hash,
	)
	from erpcore.erp_core.monthly_close import evidence

	accounts = company_bank_accounts(ctx.company)
	if not accounts:
		return Finding(
			NOT_APPLICABLE, _("The company has no enabled company bank accounts linked to a GL account.")
		)

	certifications = {}
	for name in frappe.get_all(
		BANK_CERT_DOCTYPE,
		filters={"monthly_close": ctx.close_name, "revision": ctx.revision},
		pluck="name",
	):
		cert = frappe.get_doc(BANK_CERT_DOCTYPE, name)
		certifications[cert.bank_account] = cert

	problems, unexplained_company_currency = [], 0.0
	for account in accounts:
		cert = certifications.get(account.name)
		if not cert or cert.status != "Certified":
			problems.append({"bank_account": account.name, "problem": "Not certified"})
			continue

		currency = cert.account_currency or ctx.currency
		precision = cint(cert.precision("ledger_balance")) or ctx.precision
		if cert.certified_hash != certified_content_hash(cert):
			problems.append(
				{"bank_account": account.name, "problem": "Workpaper changed after certification"}
			)

		broken = evidence.verify(cert.statement_file, cert.statement_hash)
		if broken:
			problems.append({"bank_account": account.name, "problem": f"Statement: {broken}"})

		if cert.needs_bridging() and not (cert.bridging_note or "").strip():
			problems.append(
				{"bank_account": account.name, "problem": "Statement not at month end, no bridging"}
			)

		ledger_now = flt(
			get_balance_on(account.account, ctx.period_end, company=ctx.company, in_account_currency=True),
			precision,
		)
		if flt(cert.ledger_balance, precision) != ledger_now:
			problems.append(
				{
					"bank_account": account.name,
					"problem": "Ledger changed after certification",
					"currency": currency,
					"certified": flt(cert.ledger_balance, precision),
					"ledger_now": ledger_now,
				}
			)

		difference = flt(cert.unexplained_difference, precision)
		if difference:
			same_currency = currency == ctx.currency
			if same_currency:
				unexplained_company_currency += abs(difference)
			# A company-currency tolerance says nothing about another currency.
			if not same_currency or abs(difference) > abs(flt(ctx.tolerance)):
				problems.append(
					{
						"bank_account": account.name,
						"problem": "Unexplained difference",
						"currency": currency,
						"amount": difference,
					}
				)

	if not problems:
		return Finding(
			PASSED, _("All {0} bank accounts are certified for this revision.").format(len(accounts))
		)

	return Finding(
		FINDING,
		_("{0} of {1} bank accounts are not ready.").format(
			len({p["bank_account"] for p in problems}), len(accounts)
		),
		count=len(problems),
		amount=flt(unexplained_company_currency, ctx.precision),
		samples=bounded(problems, ctx.sample_limit),
		identities=problems,
		route="monthly-close-bank-certification",
		# Tolerance was applied per account above, in the account's own currency.
		tolerance_applies=False,
	)


@register_check(
	"cheque_readiness",
	version=1,
	label="Cheque register readiness",
	default_severity=WARNING,
	description="erpcore cheque register: bounced or stopped cheques still on a submitted voucher, reservations without a live draft voucher, and register clearance dates that disagree with the voucher. The register is never treated as bank or GL evidence.",
)
def cheque_readiness(ctx: CheckContext) -> Finding:
	if not frappe.db.exists("DocType", "Cheque Leaf"):
		return Finding(NOT_APPLICABLE, _("Cheque Management is not installed."))

	if not frappe.db.exists("Cheque Leaf", {"company": ctx.company, "status": ["!=", "Unused"]}):
		return Finding(NOT_APPLICABLE, _("The company has not used any cheque leaves."))

	leaf = frappe.qb.DocType("Cheque Leaf")
	samples, notes = [], []

	outstanding = (
		frappe.qb.from_(leaf)
		.select(leaf.name, leaf.amount)
		.where(leaf.company == ctx.company)
		.where(leaf.status.isin([LEAF_ISSUED, LEAF_CLEARED]))
		.where(leaf.issue_date <= ctx.period_end)
		.where(leaf.clearance_date.isnull() | (leaf.clearance_date > ctx.period_end))
		.run(as_dict=True)
	)
	notes.append(
		_(
			"{0} cheques issued by month end were not cleared by month end (total {1}). This is normal and appears in the bank certification."
		).format(len(outstanding), flt(sum(flt(r.amount) for r in outstanding), ctx.precision))
	)

	findings = 0
	for voucher_doctype in VOUCHER_DOCTYPES:
		voucher = frappe.qb.DocType(voucher_doctype)

		unresolved = (
			frappe.qb.from_(leaf)
			.inner_join(voucher)
			.on(voucher.name == leaf.reference_name)
			.select(leaf.name, leaf.status, leaf.reference_name, leaf.amount)
			.where(leaf.company == ctx.company)
			.where(leaf.reference_doctype == voucher_doctype)
			.where(leaf.status.isin([LEAF_BOUNCED, LEAF_STOPPED]))
			.where(voucher.docstatus == 1)
			.where(voucher.posting_date <= ctx.period_end)
			.run(as_dict=True)
		)
		findings += len(unresolved)
		samples += [{"type": _("Bounced/stopped on submitted voucher"), **r} for r in unresolved]

		mismatched = (
			frappe.qb.from_(leaf)
			.inner_join(voucher)
			.on(voucher.name == leaf.reference_name)
			.select(leaf.name, leaf.clearance_date, voucher.clearance_date.as_("voucher_clearance_date"))
			.where(leaf.company == ctx.company)
			.where(leaf.reference_doctype == voucher_doctype)
			.where(leaf.status.isin([LEAF_ISSUED, LEAF_CLEARED]))
			.where(voucher.posting_date[ctx.period_start : ctx.period_end])
			.where(
				(voucher.clearance_date.isnull() & leaf.clearance_date.notnull())
				| (voucher.clearance_date.notnull() & leaf.clearance_date.isnull())
				| (voucher.clearance_date != leaf.clearance_date)
			)
			.run(as_dict=True)
		)
		findings += len(mismatched)
		samples += [{"type": _("Clearance date mismatch"), **r} for r in mismatched]

	reserved = frappe.get_all(
		"Cheque Leaf",
		filters={"company": ctx.company, "status": LEAF_RESERVED},
		fields=["name", "reference_doctype", "reference_name"],
	)
	for row in reserved:
		docstatus = (
			frappe.db.get_value(row.reference_doctype, row.reference_name, "docstatus")
			if row.reference_doctype and row.reference_name
			else None
		)
		if docstatus != 0:
			findings += 1
			samples.append({"type": _("Reservation without a live draft voucher"), **row})

	if frappe.db.exists("DocType", "Cheque Receipt"):
		pending = frappe.db.count(
			"Cheque Receipt",
			{
				"company": ctx.company,
				"docstatus": 1,
				"status": ["in", ["Received", "Deposited"]],
				"cheque_date": ["<=", ctx.period_end],
			},
		)
		notes.append(_("{0} received cheques dated by month end are not cleared yet.").format(pending))
	else:
		notes.append(_("Cheque Receipt is not installed, so inward cheques are not checked."))

	return Finding(
		FINDING if findings else PASSED,
		" ".join(notes),
		count=findings,
		samples=bounded(samples, ctx.sample_limit),
		identities=samples,
		route="cheque-leaf",
	)
