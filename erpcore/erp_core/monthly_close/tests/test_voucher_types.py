# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Submit and cancel of each common voucher type against a closed month.

For every type: one voucher is submitted into the month before it is closed,
then, after the hard close, a new voucher dated in the month and the
cancellation of the earlier one must both be refused by the month lock
(ERPNext's Accounting Period or erpcore's ledger gate), not by an unrelated
validation. An amendment is a new voucher dated in the month, so it takes the
submit path. Backs the "Covered" rows of the Posting Coverage report.
"""

import frappe
from erpnext.accounts.doctype.accounting_period.accounting_period import ClosedAccountingPeriod
from frappe.utils import add_days, get_last_day, getdate

from erpcore.erp_core.monthly_close.posting_guard import ClosedMonthError
from erpcore.erp_core.monthly_close.tests.utils import MonthlyCloseTestCase, drive_to_closed, month

WAREHOUSE = "_Test Warehouse - _TC"
LOCK_ERRORS = (ClosedMonthError, ClosedAccountingPeriod)


def sales_invoice(posting_date):
	from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

	return create_sales_invoice(posting_date=posting_date)


def purchase_invoice(posting_date):
	from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice

	# The helper does not set set_posting_time, so ERPNext would re-date the invoice to today.
	invoice = make_purchase_invoice(posting_date=posting_date, do_not_save=True)
	invoice.set_posting_time = 1
	invoice.insert()
	invoice.submit()
	return invoice


def payment_entry(posting_date):
	from erpnext.accounts.doctype.payment_entry.test_payment_entry import create_payment_entry

	entry = create_payment_entry(paid_amount=300)
	entry.posting_date = posting_date
	entry.reference_date = posting_date
	entry.save()
	entry.submit()
	return entry


def stock_entry(posting_date):
	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

	return make_stock_entry(
		item_code="_Test Item", qty=2, rate=10, to_warehouse=WAREHOUSE, posting_date=posting_date
	)


def purchase_receipt(posting_date):
	from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt

	return make_purchase_receipt(posting_date=posting_date, qty=3, rate=10)


def delivery_note(posting_date):
	from erpnext.stock.doctype.delivery_note.test_delivery_note import create_delivery_note

	stock_entry(add_days(posting_date, -1))  # stock to deliver
	return create_delivery_note(posting_date=posting_date, qty=1, rate=20)


def stock_reconciliation(posting_date):
	from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import (
		create_stock_reconciliation,
	)

	# A different quantity each time: an unchanged reconciliation is rejected as empty.
	return create_stock_reconciliation(
		item_code="_Test Item",
		warehouse=WAREHOUSE,
		qty=getdate(posting_date).day + 7,
		rate=11,
		posting_date=posting_date,
	)


FACTORIES = {
	"Sales Invoice": sales_invoice,
	"Purchase Invoice": purchase_invoice,
	"Payment Entry": payment_entry,
	"Stock Entry": stock_entry,
	"Purchase Receipt": purchase_receipt,
	"Delivery Note": delivery_note,
	"Stock Reconciliation": stock_reconciliation,
}


class TestVoucherTypes(MonthlyCloseTestCase):
	def assert_refused_by_lock(self, fn, *args):
		frappe.db.savepoint("mc_voucher")
		try:
			with self.assertRaises(LOCK_ERRORS):
				fn(*args)
		finally:
			frappe.db.rollback(save_point="mc_voucher")

	def assert_dated_in_month(self, voucher, start):
		"""Guard against helpers that silently re-date a voucher to today."""
		posting_date = getdate(frappe.db.get_value(voucher.doctype, voucher.name, "posting_date"))
		self.assertTrue(
			start <= posting_date <= get_last_day(start), f"{voucher.doctype} dated {posting_date}"
		)

	def test_submit_and_cancel_into_closed_month_are_refused(self):
		start = month(3)
		submitted = {}
		for offset, (doctype, factory) in enumerate(FACTORIES.items()):
			submitted[doctype] = factory(add_days(start, 2 + offset))
			self.assertEqual(submitted[doctype].docstatus, 1, doctype)
			self.assert_dated_in_month(submitted[doctype], start)

		drive_to_closed(3)
		gl_before = frappe.db.count("GL Entry")
		sle_before = frappe.db.count("Stock Ledger Entry")
		ple_before = frappe.db.count("Payment Ledger Entry")

		for doctype, factory in FACTORIES.items():
			with self.subTest(doctype=doctype, path="submit"):
				self.assert_refused_by_lock(factory, add_days(start, 20))
			with self.subTest(doctype=doctype, path="cancel"):
				voucher = frappe.get_doc(doctype, submitted[doctype].name)
				self.assert_refused_by_lock(voucher.cancel)
				self.assertEqual(frappe.db.get_value(doctype, voucher.name, "docstatus"), 1)

		self.assertEqual(
			(
				frappe.db.count("GL Entry"),
				frappe.db.count("Stock Ledger Entry"),
				frappe.db.count("Payment Ledger Entry"),
			),
			(gl_before, sle_before, ple_before),
			"no ledger row may be left behind by a refused voucher",
		)
