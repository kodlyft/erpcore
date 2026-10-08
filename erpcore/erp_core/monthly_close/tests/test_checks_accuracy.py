# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Accounting semantics of the close checks, waiver binding and non-waivable invariants."""

import frappe
from frappe.utils import add_days, get_last_day

from erpcore.erp_core.monthly_close import lifecycle
from erpcore.erp_core.monthly_close.checks import assets, ledger, subledgers
from erpcore.erp_core.monthly_close.checks.registry import FINDING, PASSED, CheckContext
from erpcore.erp_core.monthly_close.tests.utils import (
	PREPARER,
	REVIEWER,
	MonthlyCloseTestCase,
	as_user,
	complete_tasks,
	make_je,
	month,
	new_close,
	private_file,
	run_checks,
)
from erpcore.tests.utils import OTHER_BANK_ACCOUNT, TEST_BANK_ACCOUNT, TEST_COMPANY


def started(offset: int = 3) -> str:
	name = new_close(offset)
	with as_user(PREPARER):
		lifecycle.start_close(name)
	complete_tasks(name)
	return name


def context(close_name: str, tolerance: float = 0, sample_limit: int = 20) -> CheckContext:
	close = frappe.get_doc("Monthly Close", close_name)
	return CheckContext(
		company=close.company,
		period_start=close.period_start,
		period_end=close.period_end,
		fiscal_year=close.fiscal_year,
		close_name=close.name,
		revision=close.revision,
		currency=frappe.get_cached_value("Company", close.company, "default_currency"),
		precision=2,
		severity="Blocker",
		tolerance=tolerance,
		sample_limit=sample_limit,
		policy=frappe.get_doc("Monthly Close Policy", close.company),
	)


def insert_raw(values: dict, children: dict | None = None):
	"""Write a fixture row as it would exist in the database, without controller validation."""
	doc = frappe.get_doc(values)
	doc.db_insert()
	for fieldname, rows in (children or {}).items():
		for idx, row in enumerate(rows, start=1):
			frappe.get_doc(
				{**row, "parent": doc.name, "parenttype": doc.doctype, "parentfield": fieldname, "idx": idx}
			).db_insert()
	return doc


def result(run_name: str, check_id: str):
	run = frappe.get_doc("Monthly Close Check Run", run_name)
	return next(r for r in run.results if r.check_id == check_id)


class TestChecksAccuracy(MonthlyCloseTestCase):
	def test_unbalanced_ledger_cannot_be_waived(self):
		name = started()
		insert_raw(
			{
				"doctype": "GL Entry",
				"name": "MC-TEST-UNBALANCED",
				"company": TEST_COMPANY,
				"posting_date": add_days(month(3), 3),
				"account": "_Test Cash - _TC",
				"debit": 10,
				"debit_in_account_currency": 10,
				"voucher_type": "Journal Entry",
				"voucher_no": "MC-TEST-UNBALANCED",
				"is_cancelled": 0,
			}
		)
		row = result(run_checks(name), "trial_balance")
		self.assertEqual(row.status, "Blocker")
		self.assertFalse(row.waivable)
		with as_user(PREPARER):
			self.assertRaisesRegex(
				frappe.ValidationError, "cannot be waived", lifecycle.request_waiver, name, row.name, "Known"
			)

	def test_unfinished_repost_is_non_waivable_blocker(self):
		name = started()
		insert_raw(
			{
				"doctype": "Repost Item Valuation",
				"name": "MC-TEST-RIV",
				"company": TEST_COMPANY,
				"posting_date": add_days(month(3), 3),
				"posting_time": "00:00:01",
				"based_on": "Item and Warehouse",
				"item_code": "_Test Item",
				"warehouse": "_Test Warehouse - _TC",
				"status": "Queued",
				"docstatus": 1,
			}
		)
		run = run_checks(name)
		row = result(run, "stock_accounting")
		# The test policy runs this check as a Warning; unfinished reposts still block.
		self.assertEqual(row.status, "Blocker")
		self.assertFalse(row.waivable)
		self.assertIn(
			row.name,
			[
				r.name
				for r in lifecycle.blocking_results(
					frappe.get_doc("Monthly Close Check Run", run), frappe.get_doc("Monthly Close", name)
				)
			],
		)

	def test_changed_unsampled_finding_does_not_inherit_waiver(self):
		frappe.db.set_single_value("Monthly Close Settings", "max_sample_rows", 1)
		start = month(3)
		name = started()
		make_je(add_days(start, 2), 100, submit=False)
		second = make_je(add_days(start, 4), 50, submit=False)
		first_row = result(run_checks(name), "draft_vouchers")
		self.assertEqual(first_row.count, 2)
		self.assertEqual(len(frappe.parse_json(first_row.samples)), 1)
		self.assertEqual(first_row.identity_count, 2)

		with as_user(PREPARER):
			waiver = lifecycle.request_waiver(name, first_row.name, "Two recurring drafts, reviewed")
		with as_user(REVIEWER):
			lifecycle.decide_waiver(waiver, True, "ok")

		# Replace the draft outside the sample with a different one: same count, same total,
		# same sample. Only the unsampled row changed.
		frappe.delete_doc("Journal Entry", second.name, ignore_permissions=True)
		make_je(add_days(start, 4), 50, submit=False)
		second_row = result(run_checks(name), "draft_vouchers")
		self.assertEqual(
			(second_row.count, second_row.amount, second_row.samples),
			(first_row.count, first_row.amount, first_row.samples),
		)
		self.assertNotEqual(second_row.finding_signature, first_row.finding_signature)
		with as_user(PREPARER):
			self.assertRaisesRegex(
				frappe.ValidationError, "block the close", lifecycle.submit_for_review, name
			)

	def test_revaluation_elsewhere_in_month_is_not_proof(self):
		name = started()
		start, end = month(3), get_last_day(month(3))
		usd = "_Test Bank USD - _TC"
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": TEST_COMPANY,
				"posting_date": add_days(start, 1),
				"multi_currency": 1,
				"accounts": [
					{"account": usd, "debit_in_account_currency": 100, "exchange_rate": 80},
					{"account": "_Test Bank - _TC", "credit_in_account_currency": 8000, "exchange_rate": 1},
				],
			}
		).insert(ignore_permissions=True)
		je.submit()

		def revaluation(rev_name, posting_date, accounts):
			insert_raw(
				{
					"doctype": "Exchange Rate Revaluation",
					"name": rev_name,
					"company": TEST_COMPANY,
					"posting_date": posting_date,
					"docstatus": 1,
					"total_gain_loss": 0,
				},
				{
					"accounts": [
						{"doctype": "Exchange Rate Revaluation Account", "account": a} for a in accounts
					]
				},
			)

		revaluation("MC-TEST-ERR-MID", add_days(start, 14), [usd])
		finding = ledger.fx_revaluation(context(name))
		self.assertEqual(finding.outcome, FINDING)
		self.assertIn("month end", finding.message)

		revaluation("MC-TEST-ERR-PARTIAL", end, ["_Test Receivable USD - _TC"])
		finding = ledger.fx_revaluation(context(name))
		self.assertEqual(finding.outcome, FINDING)
		self.assertIn(usd, [row.get("not_revalued", {}).get("account") for row in finding.identities])

		revaluation("MC-TEST-ERR-FULL", end, [usd])
		self.assertEqual(ledger.fx_revaluation(context(name)).outcome, PASSED)

	def test_old_deferred_line_and_draft_booking(self):
		name = started()
		invoice = insert_raw(
			{
				"doctype": "Sales Invoice",
				"name": "MC-TEST-SINV-DEFERRED",
				"company": TEST_COMPANY,
				"customer": "_Test Customer",
				"posting_date": month(8),
				"docstatus": 1,
			},
			{
				"items": [
					{
						"doctype": "Sales Invoice Item",
						"name": "MC-TEST-SINV-DEFERRED-1",
						"enable_deferred_revenue": 1,
						"deferred_revenue_account": "Deferred Revenue - _TC",
						"service_start_date": month(8),
						# Service ended months before the close: older versions skipped it.
						"service_end_date": get_last_day(month(6)),
						"base_net_amount": 300,
					}
				]
			},
		)
		finding = assets.deferred_due(context(name))
		self.assertEqual(finding.outcome, FINDING)
		self.assertEqual([p["invoice"] for p in finding.identities], [invoice.name])

		# A draft journal for the line posts nothing; ERPNext's booking helper would count it.
		insert_raw(
			{
				"doctype": "Journal Entry",
				"name": "MC-TEST-JE-DRAFT-DEFERRED",
				"company": TEST_COMPANY,
				"posting_date": get_last_day(month(6)),
				"docstatus": 0,
			},
			{
				"accounts": [
					{
						"doctype": "Journal Entry Account",
						"account": "Deferred Revenue - _TC",
						"reference_type": "Sales Invoice",
						"reference_name": invoice.name,
						"reference_detail_no": "MC-TEST-SINV-DEFERRED-1",
						"docstatus": 0,
					}
				]
			},
		)
		finding = assets.deferred_due(context(name))
		self.assertEqual(finding.outcome, FINDING)
		self.assertIn("draft", finding.message)
		self.assertEqual(finding.identities[0]["draft_journals"], "MC-TEST-JE-DRAFT-DEFERRED")

	def test_foreign_currency_bank_difference_ignores_company_tolerance(self):
		name = started()
		end = get_last_day(month(3))
		for bank_account in (TEST_BANK_ACCOUNT, OTHER_BANK_ACCOUNT):
			with as_user(PREPARER):
				cert = frappe.get_doc(
					{
						"doctype": "Monthly Close Bank Certification",
						"monthly_close": name,
						"bank_account": bank_account,
					}
				).insert()
				cert.statement_file = private_file(
					f"{cert.name}.txt", cert.name.encode(), ("Monthly Close Bank Certification", cert.name)
				)
				cert.statement_date = end
				cert.statement_balance = cert.expected_statement_balance + 5
				cert.difference_explanation = "Bank charge not yet booked"
				cert.save()
			with as_user(REVIEWER):
				lifecycle.certify_bank_account(cert.name)

		finding = subledgers.bank_reconciliation(context(name, tolerance=1000))
		unexplained = {
			p["bank_account"]: p for p in finding.identities if p["problem"] == "Unexplained difference"
		}
		# 5 INR is inside a 1000 INR tolerance; 5 USD is not comparable with it.
		self.assertNotIn(TEST_BANK_ACCOUNT, unexplained)
		self.assertIn(OTHER_BANK_ACCOUNT, unexplained)
		self.assertEqual(unexplained[OTHER_BANK_ACCOUNT]["currency"], "USD")
		self.assertEqual(finding.amount, 5, "only company-currency differences are summed")
