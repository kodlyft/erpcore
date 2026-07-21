# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe

from erpcore.erp_core.cheque_constants import (
	LEAF_BOUNCED,
	LEAF_CLEARED,
	LEAF_ISSUED,
	LEAF_LOST,
	LEAF_RESERVED,
	LEAF_STOPPED,
	LEAF_UNUSED,
	LEAF_VOID,
)
from erpcore.erp_core.doctype.cheque_leaf.cheque_leaf import get_available_leaves
from erpcore.tests.utils import (
	OTHER_BANK_ACCOUNT,
	PLAIN_USER,
	TEST_BANK_ACCOUNT,
	TEST_COMPANY,
	ERPCoreTestCase,
	get_leaf,
	make_cheque_book,
)

VOID_REASON = "Spoiled / Misprint"


class IntegrationTestChequeLeaf(ERPCoreTestCase):
	def setUp(self):
		self.book = make_cheque_book(number_of_leaves=3)
		self.leaf = get_leaf(self.book.name)

	def test_autoname_combines_book_and_cheque_no(self):
		self.assertEqual(self.leaf.name, f"{self.book.name}-{self.leaf.cheque_no}")

	def test_used_leaf_cannot_be_deleted(self):
		self.leaf.db_set("status", LEAF_ISSUED)
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, "Cheque Leaf", self.leaf.name)

	def test_unused_leaf_can_be_deleted(self):
		frappe.delete_doc("Cheque Leaf", self.leaf.name)
		self.assertFalse(frappe.db.exists("Cheque Leaf", self.leaf.name))

	def test_void_leaf(self):
		self.leaf.void_leaf(reason=VOID_REASON, remarks="printer jam")
		self.leaf.reload()

		self.assertEqual(self.leaf.status, LEAF_VOID)
		self.assertEqual(self.leaf.void_reason, VOID_REASON)
		self.assertEqual(self.leaf.voided_by, frappe.session.user)
		self.assertEqual(self.leaf.void_remarks, "printer jam")
		self.assertTrue(self.leaf.void_date)

	def test_void_requires_a_reason_by_default(self):
		self.assertRaises(frappe.ValidationError, self.leaf.void_leaf)

	def test_void_accepts_no_reason_when_setting_is_off(self):
		with self.change_settings("Cheque Settings", require_void_reason=0):
			self.leaf.void_leaf()

		self.assertEqual(self.leaf.status, LEAF_VOID)

	def test_void_rejects_an_unknown_reason(self):
		self.assertRaises(frappe.ValidationError, self.leaf.void_leaf, reason="_Not A Reason")

	def test_issued_leaf_cannot_be_voided(self):
		self.leaf.db_set("status", LEAF_ISSUED)
		self.assertRaises(frappe.ValidationError, self.leaf.void_leaf, reason=VOID_REASON)

	def test_leaf_reserved_by_a_draft_cannot_be_voided(self):
		self.leaf.db_set(
			{
				"status": LEAF_RESERVED,
				"reference_doctype": "Payment Entry",
				"reference_name": "_Test PE",
			}
		)
		self.assertRaises(frappe.ValidationError, self.leaf.void_leaf, reason=VOID_REASON)

	def test_mark_lost(self):
		self.leaf.mark_lost(reason=VOID_REASON)
		self.assertEqual(self.leaf.status, LEAF_LOST)

	def test_issued_leaf_cannot_be_marked_lost(self):
		self.leaf.db_set("status", LEAF_ISSUED)
		self.assertRaises(frappe.ValidationError, self.leaf.mark_lost, reason=VOID_REASON)

	def test_mark_stopped_needs_an_issued_leaf(self):
		self.assertRaises(frappe.ValidationError, self.leaf.mark_stopped, reason=VOID_REASON)

		self.leaf.db_set("status", LEAF_ISSUED)
		self.leaf.mark_stopped(reason=VOID_REASON)
		self.assertEqual(self.leaf.status, LEAF_STOPPED)

	def test_mark_bounced_accepts_issued_and_cleared(self):
		self.leaf.db_set("status", LEAF_CLEARED)
		self.leaf.mark_bounced(reason="Insufficient Funds")
		self.assertEqual(self.leaf.status, LEAF_BOUNCED)

	def test_unused_leaf_cannot_bounce(self):
		self.assertRaises(frappe.ValidationError, self.leaf.mark_bounced, reason="Insufficient Funds")

	def test_voided_leaf_cannot_be_reused_by_default(self):
		self.leaf.void_leaf(reason=VOID_REASON)
		self.assertRaises(frappe.ValidationError, self.leaf.revert_to_unused)

	def test_voided_leaf_can_be_reused_when_allowed(self):
		self.leaf.void_leaf(reason=VOID_REASON)
		self.leaf.db_set({"amount": 500, "payee_name": "_Test Payee"})

		with self.change_settings("Cheque Settings", allow_void_leaf_reuse=1):
			self.leaf.revert_to_unused(remarks="stationery reprinted")

		self.leaf.reload()
		self.assertEqual(self.leaf.status, LEAF_UNUSED)
		self.assertEqual(self.leaf.amount, 0)
		self.assertIsNone(self.leaf.payee_name)
		self.assertIsNone(self.leaf.void_reason)

	def test_lost_leaf_can_always_be_reverted(self):
		self.leaf.mark_lost(reason=VOID_REASON)
		self.leaf.revert_to_unused()
		self.assertEqual(self.leaf.status, LEAF_UNUSED)

	def test_issued_leaf_cannot_be_reverted(self):
		self.leaf.db_set("status", LEAF_ISSUED)
		self.assertRaises(frappe.ValidationError, self.leaf.revert_to_unused)

	def test_status_actions_need_write_permission(self):
		with self.set_user(PLAIN_USER):
			leaf = frappe.get_doc("Cheque Leaf", self.leaf.name)
			self.assertRaises(frappe.PermissionError, leaf.void_leaf, reason=VOID_REASON)

	def _available(self, **filters):
		rows = get_available_leaves("Cheque Leaf", "", "name", 0, 20, filters)
		return [row[0] for row in rows]

	def test_available_leaves_lists_unused_leaves_of_active_books(self):
		self.assertIn(self.leaf.name, self._available(company=TEST_COMPANY))

	def test_available_leaves_hides_consumed_leaves(self):
		self.leaf.db_set("status", LEAF_ISSUED)
		self.assertNotIn(self.leaf.name, self._available(company=TEST_COMPANY))

	def test_available_leaves_includes_the_voucher_own_reservation(self):
		self.leaf.db_set(
			{
				"status": LEAF_RESERVED,
				"reference_doctype": "Payment Entry",
				"reference_name": "_Test PE",
			}
		)

		self.assertNotIn(self.leaf.name, self._available(company=TEST_COMPANY))
		self.assertIn(self.leaf.name, self._available(company=TEST_COMPANY, voucher_name="_Test PE"))

	def test_available_leaves_respects_the_bank_account_filter(self):
		self.assertIn(self.leaf.name, self._available(bank_account=TEST_BANK_ACCOUNT))
		self.assertNotIn(self.leaf.name, self._available(bank_account=OTHER_BANK_ACCOUNT))
