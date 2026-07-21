# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe
from erpnext.accounts.doctype.payment_entry.test_payment_entry import create_payment_entry
from frappe.utils import add_days, nowdate

from erpcore.erp_core.cheque_constants import LEAF_CLEARED, LEAF_ISSUED
from erpcore.erp_core.tasks import (
	expire_stale_visitor_passes,
	notify_overdue_gate_pass_returns,
	notify_pdc_due,
	reconcile_cheque_leaf_status,
	update_gate_pass_overdue_status,
)
from erpcore.tests.utils import (
	GUARD_USER,
	ERPCoreTestCase,
	get_leaf,
	make_cheque_book,
	make_gate_pass,
)


class TestTasks(ERPCoreTestCase):
	def issued_leaf(self):
		book = make_cheque_book(number_of_leaves=2)
		leaf = get_leaf(book.name)

		payment = create_payment_entry()
		payment.cheque_leaf = leaf.name
		payment.save()
		payment.submit()

		return leaf, payment

	def test_reconcile_clears_a_leaf_whose_voucher_cleared(self):
		leaf, payment = self.issued_leaf()
		# bypass the on_change hook so the task has something to repair
		frappe.db.set_value("Payment Entry", payment.name, "clearance_date", nowdate())

		reconcile_cheque_leaf_status()

		self.assertEqual(frappe.db.get_value("Cheque Leaf", leaf.name, "status"), LEAF_CLEARED)

	def test_reconcile_reopens_a_leaf_whose_clearance_was_undone(self):
		leaf, _ = self.issued_leaf()
		frappe.db.set_value("Cheque Leaf", leaf.name, {"status": LEAF_CLEARED, "clearance_date": nowdate()})

		reconcile_cheque_leaf_status()

		self.assertEqual(frappe.db.get_value("Cheque Leaf", leaf.name, "status"), LEAF_ISSUED)

	def test_reconcile_does_nothing_when_the_setting_is_off(self):
		leaf, payment = self.issued_leaf()
		frappe.db.set_value("Payment Entry", payment.name, "clearance_date", nowdate())

		with self.change_settings("Cheque Settings", auto_set_cleared_from_clearance_date=0):
			frappe.clear_cache()
			reconcile_cheque_leaf_status()

		self.assertEqual(frappe.db.get_value("Cheque Leaf", leaf.name, "status"), LEAF_ISSUED)

	def test_overdue_gate_passes_are_flagged(self):
		doc = make_gate_pass(
			returnable=1,
			posting_date=add_days(nowdate(), -10),
			expected_return_date=add_days(nowdate(), -2),
		)
		doc.verify()
		doc.reload()
		doc.check_out()
		frappe.db.set_value("Gate Pass", doc.name, "status", "Exited")

		update_gate_pass_overdue_status()

		self.assertEqual(frappe.db.get_value("Gate Pass", doc.name, "status"), "Overdue")

	def test_a_pass_still_in_date_is_left_alone(self):
		doc = make_gate_pass(returnable=1)
		doc.verify()
		doc.reload()
		doc.check_out()

		update_gate_pass_overdue_status()

		self.assertNotEqual(frappe.db.get_value("Gate Pass", doc.name, "status"), "Overdue")

	def test_overdue_digest_reaches_the_configured_roles(self):
		doc = make_gate_pass(
			returnable=1,
			posting_date=add_days(nowdate(), -10),
			expected_return_date=add_days(nowdate(), -2),
		)
		doc.verify()
		doc.reload()
		doc.check_out()
		frappe.db.set_value("Gate Pass", doc.name, "status", "Overdue")

		before = frappe.db.count("Email Queue")
		self.set_notify_roles(["Gate Keeper"])  # GUARD_USER holds this role

		notify_overdue_gate_pass_returns()

		self.assertGreater(frappe.db.count("Email Queue"), before)
		self.assertIn(
			GUARD_USER,
			frappe.get_all("Email Queue Recipient", pluck="recipient"),
		)

	def test_no_digest_without_configured_roles(self):
		before = frappe.db.count("Email Queue")
		self.set_notify_roles([])

		notify_overdue_gate_pass_returns()

		self.assertEqual(frappe.db.count("Email Queue"), before)

	def set_notify_roles(self, roles):
		"""change_settings cannot write a child table, so do it the long way."""
		settings = frappe.get_doc("Gate Pass Settings")
		settings.notify_roles = []
		for role in roles:
			settings.append("notify_roles", {"role": role})
		settings.save()
		frappe.clear_cache()

	def test_pdc_alert_is_inert_without_the_cheque_receipt_doctype(self):
		# Cheque Receipt is referenced but not shipped; the daily job must not blow up
		self.assertFalse(frappe.db.exists("DocType", "Cheque Receipt"))
		notify_pdc_due()

	def test_visitor_expiry_is_inert_without_the_visitor_gate_pass_doctype(self):
		self.assertFalse(frappe.db.exists("DocType", "Visitor Gate Pass"))
		expire_stale_visitor_passes()
