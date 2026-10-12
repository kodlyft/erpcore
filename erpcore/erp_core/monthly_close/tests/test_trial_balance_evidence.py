# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""The close's Trial Balance evidence compared with ERPNext's native Trial Balance.

* The sealed Trial Balance snapshot is the native report's own output: rerunning
  the native report with the snapshot's stored filters reproduces it exactly.
* The close's trial-balance check and the native report agree on balance per
  finance book, on opening balances and on the company currency's precision.

Intentional differences, asserted below rather than hidden:

1. Finance books. A native finance-book filter always includes rows with no
   finance book (the default book), and `include_default_book_entries` adds the
   company's default finance book as well. The check instead groups each book on
   its own (no-book rows separately). A voucher split between a named book and
   the default book therefore looks balanced in the native view of that named
   book (both halves are in it) and shows only in the native default-book view;
   the check reports it and names the book.
2. `is_opening` entries dated inside the month. The native report treats them as
   opening (Accounts Settings "Ignore Is Opening check" off); the check groups by
   posting date, so they count as month movement. Totals over opening + month
   agree either way.
"""

import json

import frappe
from frappe.utils import add_days, flt, get_last_day

from erpcore.erp_core.monthly_close.checks import ledger
from erpcore.erp_core.monthly_close.tests.test_checks_accuracy import context, gl_pair, started
from erpcore.erp_core.monthly_close.tests.utils import MonthlyCloseTestCase, drive_to_closed, month
from erpcore.tests.utils import TEST_COMPANY

FB1 = "Test Finance Book 1"
FB2 = "Test Finance Book 2"
PRECISION = 2


def journal(posting_date, amount, finance_book=None, is_opening="No"):
	cost_center = frappe.get_cached_value("Company", TEST_COMPANY, "cost_center")
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"company": TEST_COMPANY,
			"posting_date": posting_date,
			"finance_book": finance_book,
			"is_opening": is_opening,
			"accounts": [
				{
					"account": "_Test Bank - _TC",
					"debit_in_account_currency": amount,
					"cost_center": cost_center,
				},
				{
					"account": "_Test Cash - _TC",
					"credit_in_account_currency": amount,
					"cost_center": cost_center,
				},
			],
		}
	).insert(ignore_permissions=True)
	je.submit()
	return je


def native_total(start, end, **filters):
	from erpnext.accounts.report.trial_balance.trial_balance import execute

	values = frappe._dict(
		company=TEST_COMPANY,
		fiscal_year=frappe.db.get_value(
			"Fiscal Year",
			{"year_start_date": ["<=", start], "year_end_date": [">=", end]},
			"name",
		),
		from_date=start,
		to_date=end,
		show_zero_values=0,
		**filters,
	)
	rows = execute(values)[1]
	return frappe._dict(next(r for r in rows if r.get("account") == "'Total'"))


def gl_sums(start, end, books, opening_rows=None):
	"""Raw GL sums for rows whose finance book is in `books` (None = no book)."""
	gle = frappe.qb.DocType("GL Entry")
	named = [b for b in books if b]
	condition = gle.finance_book.isnull() | (gle.finance_book == "") if None in books else None
	if named:
		named_condition = gle.finance_book.isin(named)
		condition = named_condition if condition is None else (condition | named_condition)
	query = (
		frappe.qb.from_(gle)
		.select(gle.debit, gle.credit, gle.posting_date, gle.is_opening)
		.where(gle.company == TEST_COMPANY)
		.where(gle.is_cancelled == 0)
		.where(gle.posting_date <= end)
		.where(condition)
	)
	result = frappe._dict(brought_forward=0.0, month_debit=0.0, month_credit=0.0, opening_in_month=0.0)
	for row in query.run(as_dict=True):
		if row.posting_date < start:
			result.brought_forward += flt(row.debit) - flt(row.credit)
		else:
			result.month_debit += flt(row.debit)
			result.month_credit += flt(row.credit)
			if row.is_opening == "Yes":
				result.opening_in_month += flt(row.debit)
	return result


class TestTrialBalanceEvidence(MonthlyCloseTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Accounts Settings", "ignore_is_opening_check_for_reporting", 0)
		self.start = month(3)
		self.end = get_last_day(self.start)
		journal(add_days(month(5), 3), 1000)  # opening balance, default book
		journal(add_days(month(5), 4), 70, FB1)  # opening balance, book 1
		journal(add_days(self.start, 2), 200, FB1)
		journal(add_days(self.start, 3), 300, FB2)
		journal(add_days(self.start, 4), 45.67)
		journal(add_days(self.start, 5), 12, is_opening="Yes")  # opening entry dated in the month

	def test_sealed_snapshot_is_the_native_report_with_identical_filters(self):
		name = drive_to_closed(3)
		snap = frappe.db.get_value(
			"Monthly Close Snapshot",
			{"monthly_close": name, "report_name": "Trial Balance", "scope": "Month"},
			["file", "filters_json"],
			as_dict=True,
		)
		sealed = json.loads(frappe.get_doc("File", {"file_url": snap.file}).get_content())

		from erpnext.accounts.report.trial_balance.trial_balance import execute

		rows = execute(frappe._dict(json.loads(snap.filters_json)))[1]
		self.assertEqual(sealed["rows"], json.loads(json.dumps(rows, default=str, sort_keys=True)))
		self.assertEqual(sealed["filters"], json.loads(snap.filters_json))
		self.assertTrue(sealed["filters"]["include_default_book_entries"])

	def test_books_opening_and_movement_agree_with_native_totals(self):
		for book in (None, FB1, FB2):
			with self.subTest(finance_book=book):
				native = native_total(self.start, self.end, finance_book=book, include_default_book_entries=0)
				# A native book view = that book + rows with no book (difference 1 in the module docstring).
				raw = gl_sums(self.start, self.end, {None, book})
				self.assertEqual(
					flt(native.opening_debit - native.opening_credit, PRECISION),
					flt(raw.brought_forward, PRECISION),
				)
				# The opening entry dated in the month is opening natively, movement in the check (difference 2).
				self.assertEqual(
					flt(native.debit, PRECISION), flt(raw.month_debit - raw.opening_in_month, PRECISION)
				)
				self.assertEqual(flt(native.debit, PRECISION), flt(native.credit, PRECISION))
				self.assertEqual(
					flt(native.closing_debit - native.closing_credit, PRECISION),
					flt(raw.brought_forward + raw.month_debit - raw.month_credit, PRECISION),
				)

		name = started()
		self.assertEqual(ledger.trial_balance(context(name)).outcome, "Passed")

	def test_default_book_inclusion(self):
		frappe.db.set_value("Company", TEST_COMPANY, "default_finance_book", FB2)
		frappe.clear_document_cache("Company", TEST_COMPANY)
		native = native_total(self.start, self.end, include_default_book_entries=1)
		raw = gl_sums(self.start, self.end, {None, FB2})
		self.assertEqual(flt(native.debit, PRECISION), flt(raw.month_debit - raw.opening_in_month, PRECISION))
		without = native_total(self.start, self.end, include_default_book_entries=0)
		self.assertEqual(flt(native.debit - without.debit, PRECISION), 300)

	def test_imbalance_between_books_is_named_by_the_check(self):
		name = started()
		gl_pair(
			TEST_COMPANY,
			add_days(self.start, 6),
			"_Test Cash - _TC",
			"_Test Bank - _TC",
			25,
			"MC-TB-FB",
			debit_finance_book=FB1,
		)
		finding = ledger.trial_balance(context(name))
		self.assertEqual(finding.outcome, "Finding")
		self.assertIn(FB1, {row.get("finance_book") for row in finding.identities if row.get("scope")})
		in_book_view = native_total(self.start, self.end, finance_book=FB1, include_default_book_entries=0)
		self.assertEqual(
			flt(in_book_view.debit - in_book_view.credit, PRECISION), 0, "both halves are in the FB1 view"
		)
		default_view = native_total(self.start, self.end, include_default_book_entries=0)
		self.assertEqual(flt(default_view.debit - default_view.credit, PRECISION), -25)

	def test_currency_precision(self):
		"""Below half a unit of the last decimal both agree it balances; at it both show a difference."""
		name = started()
		self.assertEqual(ledger.trial_balance(context(name)).outcome, "Passed")

		self.raw_pair(10.004, 10, "MC-TB-P1")
		native = native_total(self.start, self.end, include_default_book_entries=1)
		self.assertEqual(flt(native.debit - native.credit, PRECISION), 0)
		self.assertEqual(ledger.trial_balance(context(name)).outcome, "Passed")

		self.raw_pair(10.006, 10, "MC-TB-P2")
		native = native_total(self.start, self.end, include_default_book_entries=1)
		self.assertEqual(flt(native.debit - native.credit, PRECISION), 0.01)
		self.assertEqual(ledger.trial_balance(context(name)).outcome, "Finding")

	def raw_pair(self, debit, credit, voucher):
		from erpcore.erp_core.monthly_close.tests.test_checks_accuracy import insert_raw

		cost_center = frappe.get_cached_value("Company", TEST_COMPANY, "cost_center")
		for suffix, account, dr, cr in (
			("D", "_Test Cash - _TC", debit, 0),
			("C", "_Test Bank - _TC", 0, credit),
		):
			insert_raw(
				{
					"doctype": "GL Entry",
					"name": f"{voucher}-{suffix}",
					"company": TEST_COMPANY,
					"posting_date": add_days(self.start, 7),
					"account": account,
					"account_currency": "INR",
					"cost_center": cost_center,
					"debit": dr,
					"credit": cr,
					"debit_in_account_currency": dr,
					"credit_in_account_currency": cr,
					"voucher_type": "Journal Entry",
					"voucher_no": voucher,
					"is_cancelled": 0,
					"is_opening": "No",
				}
			)
