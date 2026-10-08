# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Write paths into a locked month, exercised with real vouchers.

Each test backs a row of the Monthly Close Posting Coverage report. A refused
voucher is rolled back to a savepoint, as the request would be, before the
ledgers are inspected.
"""

import frappe
from frappe.utils import add_days

from erpcore.erp_core.monthly_close import snapshot
from erpcore.erp_core.monthly_close.posting_guard import (
	ClosedMonthError,
	guard_advance_ledger_entry,
	guard_ledger_entry,
	guard_repost,
)
from erpcore.erp_core.monthly_close.tests.utils import (
	MonthlyCloseTestCase,
	drive_to_closed,
	make_je,
	month,
)
from erpcore.tests.utils import TEST_COMPANY

CUSTOMER = "_Test Customer"
WAREHOUSE = "_Test Warehouse - _TC"


def party_je(posting_date, amount: float, against: str | None = None, submit: bool = True):
	"""A receivable (`against` None) or a receipt allocated against an earlier receivable journal."""
	cost_center = frappe.get_cached_value("Company", TEST_COMPANY, "cost_center")
	party = {"party_type": "Customer", "party": CUSTOMER, "cost_center": cost_center}
	if against:
		rows = [
			{"account": "_Test Cash - _TC", "debit_in_account_currency": amount, "cost_center": cost_center},
			{
				"account": "Debtors - _TC",
				"credit_in_account_currency": amount,
				"reference_type": "Journal Entry",
				"reference_name": against,
				**party,
			},
		]
	else:
		rows = [
			{"account": "Debtors - _TC", "debit_in_account_currency": amount, **party},
			{"account": "Sales - _TC", "credit_in_account_currency": amount, "cost_center": cost_center},
		]
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"company": TEST_COMPANY,
			"posting_date": posting_date,
			"accounts": rows,
		}
	).insert(ignore_permissions=True)
	if submit:
		je.submit()
	return je


def refused(test, fn, *args):
	"""Assert the call is refused by the month gate, and undo its partial writes like a request would."""
	frappe.db.savepoint("mc_refused")
	try:
		with test.assertRaises(frappe.ValidationError) as caught:
			fn(*args)
	finally:
		frappe.db.rollback(save_point="mc_refused")
	return caught.exception


class TestPostingPaths(MonthlyCloseTestCase):
	def test_ledger_rows_refused_in_locked_month(self):
		start = month(3)
		receivable = party_je(add_days(start, 2), 500)
		ple_before = frappe.get_all(
			"Payment Ledger Entry",
			filters={"voucher_no": receivable.name},
			fields=["name", "delinked", "amount"],
			order_by="name",
		)
		self.assertTrue(ple_before)
		drive_to_closed(3)

		# Cancelling would delink the PLE and post reversals into the month.
		receivable.reload()
		refused(self, receivable.cancel)
		ple_after = frappe.get_all(
			"Payment Ledger Entry",
			filters={"voucher_no": receivable.name},
			fields=["name", "delinked", "amount"],
			order_by="name",
		)
		self.assertEqual(ple_after, ple_before, "the delink must be rolled back with the refused reversal")
		self.assertEqual(frappe.db.get_value("Journal Entry", receivable.name, "docstatus"), 1)

		# The gate refuses each ledger on its own, whatever submitted the row.
		for doctype in ("GL Entry", "Stock Ledger Entry", "Payment Ledger Entry"):
			row = frappe._dict(
				doctype=doctype,
				company=TEST_COMPANY,
				posting_date=add_days(start, 5),
				voucher_type="Journal Entry",
				voucher_no="X",
			)
			self.assertRaises(ClosedMonthError, guard_ledger_entry, row)

	def test_advance_ledger_dated_by_voucher(self):
		je = make_je(add_days(month(3), 1), 10)
		drive_to_closed(3)
		row = frappe._dict(
			doctype="Advance Payment Ledger Entry",
			company=TEST_COMPANY,
			voucher_type="Journal Entry",
			voucher_no=je.name,
		)
		self.assertRaises(ClosedMonthError, guard_advance_ledger_entry, row)

	def test_later_settlement_is_allowed(self):
		"""A payment dated after the closed month can settle an invoice of the closed month."""
		start = month(3)
		receivable = party_je(add_days(start, 3), 400)
		name = drive_to_closed(3)
		ar_snapshot = frappe.db.get_value(
			"Monthly Close Snapshot",
			{"monthly_close": name, "report_name": "Accounts Receivable Summary"},
			"name",
		)

		receipt = party_je(add_days(month(2), 4), 150, against=receivable.name)

		allocation = frappe.get_all(
			"Payment Ledger Entry",
			filters={"voucher_no": receipt.name, "against_voucher_no": receivable.name, "delinked": 0},
			fields=["posting_date", "amount"],
		)
		self.assertEqual(len(allocation), 1)
		self.assertGreaterEqual(allocation[0].posting_date, month(2))
		self.assertEqual(allocation[0].amount, -150)
		# The sealed packet of the closed month is untouched by the live settlement.
		self.assertTrue(snapshot.verify(ar_snapshot)["ok"])

	def test_backdated_stock_cannot_revalue_locked_month(self):
		from erpnext.stock.doctype.item.test_item import make_item
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		item = make_item("_Test MC Valued Item", {"is_stock_item": 1}).name
		other = make_item("_Test MC Other Item", {"is_stock_item": 1}).name
		make_stock_entry(
			item_code=item, qty=5, rate=10, to_warehouse=WAREHOUSE, posting_date=add_days(month(3), 5)
		)
		sle_before = frappe.get_all(
			"Stock Ledger Entry",
			filters={"item_code": item, "is_cancelled": 0},
			fields=["name", "valuation_rate", "stock_value", "qty_after_transaction"],
			order_by="name",
		)
		drive_to_closed(3)

		# Month 4 is before the cutover, so unmanaged and open, but a receipt there would
		# revalue the locked month's stock of the same item and warehouse.
		frappe.db.savepoint("mc_backdated")
		try:
			with self.assertRaises(ClosedMonthError):
				make_stock_entry(
					item_code=item, qty=3, rate=50, to_warehouse=WAREHOUSE, posting_date=add_days(month(4), 5)
				)
		finally:
			frappe.db.rollback(save_point="mc_backdated")
		self.assertEqual(
			frappe.get_all(
				"Stock Ledger Entry",
				filters={"item_code": item, "is_cancelled": 0},
				fields=["name", "valuation_rate", "stock_value", "qty_after_transaction"],
				order_by="name",
			),
			sle_before,
		)

		# An item with no stock in the locked month is not affected.
		make_stock_entry(
			item_code=other, qty=2, rate=10, to_warehouse=WAREHOUSE, posting_date=add_days(month(4), 5)
		)

	def test_reposts_into_locked_month_are_refused(self):
		from erpnext.stock.doctype.item.test_item import make_item
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		item = make_item("_Test MC Repost Item", {"is_stock_item": 1}).name
		make_stock_entry(
			item_code=item, qty=5, rate=10, to_warehouse=WAREHOUSE, posting_date=add_days(month(3), 5)
		)
		je = make_je(add_days(month(3), 2), 20)
		drive_to_closed(3)

		def riv(posting_date, item_code):
			return frappe.get_doc(
				{
					"doctype": "Repost Item Valuation",
					"company": TEST_COMPANY,
					"based_on": "Item and Warehouse",
					"item_code": item_code,
					"warehouse": WAREHOUSE,
					"posting_date": posting_date,
					"posting_time": "00:00:01",
				}
			)

		# Starts inside the locked month.
		self.assertRaises(ClosedMonthError, guard_repost, riv(add_days(month(3), 1), item))
		# Starts before it, but would revalue the locked month's stock of the item.
		self.assertRaises(ClosedMonthError, guard_repost, riv(add_days(month(4), 1), item))

		ral = frappe.get_doc(
			{
				"doctype": "Repost Accounting Ledger",
				"company": TEST_COMPANY,
				"vouchers": [{"voucher_type": "Journal Entry", "voucher_no": je.name}],
			}
		)
		self.assertRaises(ClosedMonthError, guard_repost, ral)

		rpl = frappe.get_doc(
			{
				"doctype": "Repost Payment Ledger",
				"company": TEST_COMPANY,
				"posting_date": add_days(month(4), 1),
			}
		)
		self.assertRaises(ClosedMonthError, guard_repost, rpl)

		# The hook is wired: saving the document itself is refused.
		self.assertRaises(frappe.ValidationError, riv(add_days(month(3), 1), item).insert)

	def test_two_companies_close_the_same_month(self):
		from erpcore.erp_core.monthly_close.tests.utils import OTHER_COMPANY

		first = drive_to_closed(3)
		second = drive_to_closed(3, company=OTHER_COMPANY)
		periods = [frappe.db.get_value("Monthly Close", n, "accounting_period") for n in (first, second)]
		names = [frappe.db.get_value("Accounting Period", p, "period_name") for p in periods]
		self.assertEqual(len(set(periods)), 2)
		self.assertEqual(len(set(names)), 2, names)
		for close_name, period_name in zip((first, second), names, strict=True):
			company = frappe.db.get_value("Monthly Close", close_name, "company")
			self.assertIn(company, period_name)

		# Each company's month is locked, independently of the other.
		self.assertRaises(frappe.ValidationError, make_je, add_days(month(3), 4), 10, True, TEST_COMPANY)
		self.assertRaises(frappe.ValidationError, make_je, add_days(month(3), 4), 10, True, OTHER_COMPANY)
