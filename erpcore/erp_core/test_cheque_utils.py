# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

"""
Integration between a cheque leaf and the vouchers that consume it.
"""

import frappe
from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry
from erpnext.accounts.doctype.payment_entry.test_payment_entry import create_payment_entry
from frappe.utils import add_days, nowdate

from erpcore.erp_core.cheque_constants import (
	LEAF_CLEARED,
	LEAF_ISSUED,
	LEAF_RESERVED,
	LEAF_UNUSED,
	LEAF_VOID,
	VOID_REASON_CANCELLED_VOUCHER,
)
from erpcore.erp_core.cheque_utils import voucher_validate
from erpcore.tests.utils import (
	OTHER_BANK_ACCOUNT,
	TEST_BANK_ACCOUNT,
	ERPCoreTestCase,
	get_leaf,
	make_cheque_book,
)

JOURNAL_DEBIT = "_Test Account Cost for Goods Sold - _TC"
JOURNAL_CREDIT = "_Test Bank - _TC"


class TestChequeUtils(ERPCoreTestCase):
	def setUp(self):
		self.book = make_cheque_book(number_of_leaves=3)
		self.leaf = get_leaf(self.book.name)
		self.other_leaf = get_leaf(self.book.name, 1)

	def make_payment(self, leaf=None, save=True, **args):
		payment = create_payment_entry(**args)
		payment.cheque_leaf = (leaf or self.leaf).name
		if save:
			payment.save()
		return payment

	def leaf_status(self, leaf=None):
		return frappe.db.get_value("Cheque Leaf", (leaf or self.leaf).name, "status")

	def test_leaf_overwrites_the_voucher_cheque_number(self):
		payment = self.make_payment()

		self.assertEqual(payment.reference_no, self.leaf.cheque_no)

	def test_an_existing_reference_date_is_left_alone(self):
		fixed = add_days(nowdate(), -3)
		payment = self.make_payment(save=False)
		payment.reference_date = fixed
		payment.save()

		self.assertEqual(str(payment.reference_date), fixed)

	def test_a_draft_reserves_the_leaf(self):
		payment = self.make_payment()

		leaf = frappe.db.get_value(
			"Cheque Leaf", self.leaf.name, ["status", "reference_doctype", "reference_name"], as_dict=True
		)
		self.assertEqual(leaf.status, LEAF_RESERVED)
		self.assertEqual(leaf.reference_doctype, "Payment Entry")
		self.assertEqual(leaf.reference_name, payment.name)

	def test_reservation_can_be_switched_off(self):
		with self.change_settings("Cheque Settings", reserve_leaf_on_draft=0):
			self.make_payment()

		self.assertEqual(self.leaf_status(), LEAF_UNUSED)

	def test_switching_leaf_releases_the_previous_one(self):
		payment = self.make_payment()
		self.assertEqual(self.leaf_status(), LEAF_RESERVED)

		payment.cheque_leaf = self.other_leaf.name
		payment.save()

		self.assertEqual(self.leaf_status(), LEAF_UNUSED)
		self.assertEqual(self.leaf_status(self.other_leaf), LEAF_RESERVED)

	def test_clearing_the_leaf_releases_it(self):
		payment = self.make_payment()

		payment.cheque_leaf = None
		payment.save()

		self.assertEqual(self.leaf_status(), LEAF_UNUSED)

	def test_deleting_a_draft_releases_the_leaf(self):
		payment = self.make_payment()
		payment.delete()

		self.assertEqual(self.leaf_status(), LEAF_UNUSED)

	def test_saving_twice_keeps_the_same_reservation(self):
		payment = self.make_payment()
		payment.save()

		self.assertEqual(self.leaf_status(), LEAF_RESERVED)

	def test_leaf_used_by_another_voucher_is_refused(self):
		frappe.db.set_value(
			"Cheque Leaf",
			self.leaf.name,
			{
				"status": LEAF_ISSUED,
				"reference_doctype": "Payment Entry",
				"reference_name": "_Test Other PE",
			},
		)

		self.assertRaises(frappe.ValidationError, self.make_payment)

	def test_leaf_reserved_by_another_draft_is_refused(self):
		frappe.db.set_value(
			"Cheque Leaf",
			self.leaf.name,
			{
				"status": LEAF_RESERVED,
				"reference_doctype": "Payment Entry",
				"reference_name": "_Test Other PE",
			},
		)

		self.assertRaises(frappe.ValidationError, self.make_payment)

	def test_missing_leaf_is_refused(self):
		payment = create_payment_entry()
		payment.cheque_leaf = "_Test Nonexistent Leaf"

		self.assertRaises(frappe.ValidationError, payment.save)

	def test_leaf_of_another_company_is_refused(self):
		frappe.db.set_value("Cheque Leaf", self.leaf.name, "company", "_Test Company 1")

		self.assertRaises(frappe.ValidationError, self.make_payment)

	def test_leaf_of_another_bank_account_is_refused(self):
		payment = self.make_payment(save=False)
		payment.bank_account = OTHER_BANK_ACCOUNT

		self.assertRaises(frappe.ValidationError, payment.save)

	def test_matching_bank_account_is_accepted(self):
		payment = self.make_payment(save=False)
		payment.bank_account = TEST_BANK_ACCOUNT
		payment.save()

		self.assertEqual(payment.reference_no, self.leaf.cheque_no)

	def test_unsupported_doctype_is_a_no_op(self):
		invoice = frappe.new_doc("Sales Invoice")
		invoice.cheque_leaf = self.leaf.name

		voucher_validate(invoice)

		self.assertEqual(self.leaf_status(), LEAF_UNUSED)

	def test_submit_issues_the_leaf(self):
		payment = self.make_payment()
		payment.submit()

		leaf = frappe.db.get_value(
			"Cheque Leaf",
			self.leaf.name,
			["status", "reference_name", "party_type", "party", "amount", "currency", "issue_date"],
			as_dict=True,
		)
		self.assertEqual(leaf.status, LEAF_ISSUED)
		self.assertEqual(leaf.reference_name, payment.name)
		self.assertEqual(leaf.party_type, "Supplier")
		self.assertEqual(leaf.party, payment.party)
		self.assertEqual(leaf.amount, payment.paid_amount)
		self.assertEqual(leaf.currency, payment.paid_from_account_currency)
		self.assertTrue(leaf.issue_date)

	def test_book_counts_follow_the_leaf(self):
		payment = self.make_payment()
		payment.submit()

		from erpcore.erp_core.doctype.cheque_book.cheque_book import update_counts

		update_counts(self.book.name)
		self.book.reload()

		self.assertEqual(self.book.issued_count, 1)
		self.assertEqual(self.book.unused_count, 2)

	def test_cancel_voids_the_leaf(self):
		payment = self.make_payment()
		payment.submit()
		payment.cancel()

		leaf = frappe.db.get_value(
			"Cheque Leaf", self.leaf.name, ["status", "void_reason", "void_date"], as_dict=True
		)
		self.assertEqual(leaf.status, LEAF_VOID)
		self.assertEqual(leaf.void_reason, VOID_REASON_CANCELLED_VOUCHER)
		self.assertTrue(leaf.void_date)

	def test_cancel_returns_the_leaf_when_reuse_is_allowed(self):
		payment = self.make_payment()
		payment.submit()

		with self.change_settings("Cheque Settings", allow_void_leaf_reuse=1):
			payment.cancel()

		leaf = frappe.db.get_value(
			"Cheque Leaf", self.leaf.name, ["status", "amount", "party", "reference_name"], as_dict=True
		)
		self.assertEqual(leaf.status, LEAF_UNUSED)
		self.assertEqual(leaf.amount, 0)
		self.assertIsNone(leaf.party)
		self.assertIsNone(leaf.reference_name)

	def test_clearance_date_clears_the_leaf(self):
		payment = self.make_payment()
		payment.submit()

		payment.db_set("clearance_date", nowdate())
		self.assertEqual(self.leaf_status(), LEAF_CLEARED)

		payment.db_set("clearance_date", None)
		self.assertEqual(self.leaf_status(), LEAF_ISSUED)

	def test_clearance_sync_can_be_switched_off(self):
		payment = self.make_payment()
		payment.submit()

		with self.change_settings("Cheque Settings", auto_set_cleared_from_clearance_date=0):
			payment.db_set("clearance_date", nowdate())

		self.assertEqual(self.leaf_status(), LEAF_ISSUED)

	def test_journal_entry_uses_its_own_cheque_fields(self):
		journal = make_journal_entry(JOURNAL_DEBIT, JOURNAL_CREDIT, 500, save=False)
		journal.cheque_leaf = self.leaf.name
		journal.save()

		self.assertEqual(journal.cheque_no, self.leaf.cheque_no)
		self.assertTrue(journal.cheque_date)
		self.assertEqual(self.leaf_status(), LEAF_RESERVED)

	def test_journal_entry_issues_the_leaf_on_submit(self):
		journal = make_journal_entry(JOURNAL_DEBIT, JOURNAL_CREDIT, 500, save=False)
		journal.cheque_leaf = self.leaf.name
		journal.save()
		journal.submit()

		leaf = frappe.db.get_value(
			"Cheque Leaf", self.leaf.name, ["status", "reference_doctype", "amount"], as_dict=True
		)
		self.assertEqual(leaf.status, LEAF_ISSUED)
		self.assertEqual(leaf.reference_doctype, "Journal Entry")
		self.assertEqual(leaf.amount, 500)

	def test_postdated_cheque_only_warns(self):
		frappe.db.set_value("Cheque Leaf", self.leaf.name, "cheque_date", add_days(nowdate(), 30))

		payment = self.make_payment()

		self.assertEqual(payment.docstatus, 0)
		self.assertEqual(self.leaf_status(), LEAF_RESERVED)
