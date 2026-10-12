# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Landed cost, depreciation, deferred accounting and exchange revaluation into a closed month.

Each path is driven through the function ERPNext's button or scheduler calls for
one voucher, with real masters. A refused call is rolled back to a savepoint, as
the request (or ERPNext's own job) would be, and the ledgers are compared with
their state before the attempt. The scheduler loops themselves, which commit and
roll back per document, are exercised on a disposable bench (see
docs/monthly-closing/review-coverage.md).
"""

import frappe
from erpnext.accounts.doctype.accounting_period.accounting_period import ClosedAccountingPeriod
from frappe.utils import add_days, get_last_day, getdate, nowdate

from erpcore.erp_core.monthly_close.posting_guard import ClosedMonthError
from erpcore.erp_core.monthly_close.tests.utils import MonthlyCloseTestCase, drive_to_closed, month
from erpcore.tests.utils import TEST_COMPANY

LOCK_ERRORS = (ClosedMonthError, ClosedAccountingPeriod)


def ledger_state():
	return tuple(
		frappe.db.sql(f"select count(*), coalesce(sum(debit), 0) from `tab{doctype}`")[0]
		if doctype == "GL Entry"
		else frappe.db.sql(f"select count(*) from `tab{doctype}`")[0]
		for doctype in ("GL Entry", "Stock Ledger Entry", "Payment Ledger Entry", "Repost Item Valuation")
	)


class TestScheduledPostings(MonthlyCloseTestCase):
	def assert_refused_without_residue(self, fn, *args, **kwargs):
		before = ledger_state()
		frappe.db.savepoint("mc_scheduled")
		try:
			with self.assertRaises(LOCK_ERRORS):
				fn(*args, **kwargs)
		finally:
			frappe.db.rollback(save_point="mc_scheduled")
		self.assertEqual(ledger_state(), before, "no ledger or repost row may remain")

	def test_landed_cost_cannot_revalue_a_receipt_of_the_closed_month(self):
		from erpnext.stock.doctype.landed_cost_voucher.test_landed_cost_voucher import (
			make_landed_cost_voucher,
		)
		from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt

		closed_receipt = make_purchase_receipt(posting_date=add_days(month(3), 4), qty=5, rate=100)
		self.assertEqual(getdate(closed_receipt.posting_date), getdate(add_days(month(3), 4)))
		open_receipt = make_purchase_receipt(posting_date=add_days(month(2), 4), qty=5, rate=100)
		drive_to_closed(3)
		values_before = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": closed_receipt.name},
			fields=["stock_value_difference", "incoming_rate"],
		)

		lcv = make_landed_cost_voucher(
			receipt_document_type="Purchase Receipt",
			receipt_document=closed_receipt.name,
			charges=50,
			do_not_submit=True,
		)
		self.assertEqual(getdate(lcv.posting_date), getdate(nowdate()), "the voucher itself is dated today")
		self.assert_refused_without_residue(lcv.submit)
		self.assertEqual(frappe.db.get_value("Landed Cost Voucher", lcv.name, "docstatus"), 0)
		self.assertEqual(
			frappe.get_all(
				"Stock Ledger Entry",
				filters={"voucher_no": closed_receipt.name},
				fields=["stock_value_difference", "incoming_rate"],
			),
			values_before,
		)

		# The same voucher type on a receipt of an open month is unaffected.
		allowed = make_landed_cost_voucher(
			receipt_document_type="Purchase Receipt", receipt_document=open_receipt.name, charges=50
		)
		self.assertEqual(allowed.docstatus, 1)

	def test_depreciation_due_in_the_closed_month_is_refused(self):
		from erpnext.assets.doctype.asset.depreciation import make_depreciation_entry
		from erpnext.assets.doctype.asset.test_asset import create_asset

		start = month(3)
		asset = create_asset(
			item_code="Macbook Pro",
			asset_category="Computers",
			calculate_depreciation=1,
			purchase_date=start,
			available_for_use_date=start,
			depreciation_start_date=get_last_day(start),
			frequency_of_depreciation=1,
			total_number_of_depreciations=12,
			net_purchase_amount=12000,
			purchase_amount=12000,
			submit=1,
		)
		schedule = frappe.get_doc(
			"Asset Depreciation Schedule", {"asset": asset.name, "status": "Active", "docstatus": 1}
		)
		first = schedule.depreciation_schedule[0]
		self.assertEqual(getdate(first.schedule_date), get_last_day(start))
		drive_to_closed(3)

		self.assert_refused_without_residue(make_depreciation_entry, schedule.name, get_last_day(start))
		self.assertFalse(frappe.db.get_value("Depreciation Schedule", first.name, "journal_entry"))
		self.assertFalse(
			frappe.db.exists(
				"Journal Entry Account", {"reference_type": "Asset", "reference_name": asset.name}
			)
		)

	def test_deferred_revenue_booking_into_the_closed_month_is_refused(self):
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		start = month(3)
		invoice = create_sales_invoice(posting_date=add_days(month(5), 2), rate=900, do_not_submit=True)
		invoice.items[0].enable_deferred_revenue = 1
		invoice.items[0].deferred_revenue_account = "Deferred Revenue - _TC"
		invoice.items[0].service_start_date = start
		invoice.items[0].service_end_date = get_last_day(month(1))
		invoice.save()
		invoice.submit()
		drive_to_closed(3)

		def process(posting_date):
			frappe.get_doc(
				{
					"doctype": "Process Deferred Accounting",
					"company": TEST_COMPANY,
					"type": "Income",
					"posting_date": posting_date,
					"start_date": start,
					"end_date": get_last_day(start),
				}
			).insert().submit()

		# Run today (the usual scheduler date): it books revenue dated in the closed month.
		self.assert_refused_without_residue(process, nowdate())
		# Run dated inside the closed month: the run itself is refused.
		self.assert_refused_without_residue(process, add_days(start, 10))
		self.assertFalse(
			frappe.db.exists(
				"GL Entry",
				{
					"voucher_no": invoice.name,
					"account": "Deferred Revenue - _TC",
					"posting_date": get_last_day(start),
				},
			)
		)

	def test_exchange_revaluation_journal_into_the_closed_month_is_refused(self):
		start, end = month(3), get_last_day(month(3))
		usd = "_Test Bank USD - _TC"
		frappe.get_doc(
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
		).insert(ignore_permissions=True).submit()
		frappe.get_doc(
			{
				"doctype": "Currency Exchange",
				"date": end,
				"from_currency": "USD",
				"to_currency": "INR",
				"exchange_rate": 85,
				"for_buying": 1,
				"for_selling": 1,
			}
		).insert(ignore_permissions=True)
		drive_to_closed(3)
		# As ERPNext's own revaluation tests do: unrealized gains post to the exchange gain/loss account.
		frappe.db.set_value(
			"Company",
			TEST_COMPANY,
			"unrealized_exchange_gain_loss_account",
			frappe.get_cached_value("Company", TEST_COMPANY, "exchange_gain_loss_account")
			or "Exchange Gain/Loss - _TC",
		)
		frappe.clear_document_cache("Company", TEST_COMPANY)

		err = frappe.new_doc("Exchange Rate Revaluation")
		err.company = TEST_COMPANY
		err.posting_date = end
		err.fetch_and_calculate_accounts_data()
		self.assertIn(usd, [row.account for row in err.accounts])
		err.save()
		before = ledger_state()
		err.submit()  # a revaluation document posts nothing by itself
		self.assertEqual(ledger_state(), before)

		self.assert_refused_without_residue(err.make_jv_entries)
		self.assertFalse(
			frappe.db.exists(
				"Journal Entry Account",
				{"reference_type": "Exchange Rate Revaluation", "reference_name": err.name},
			)
		)
