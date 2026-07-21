# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe

from erpcore.erp_core.cheque_constants import DEFAULT_VOID_REASONS, VOID_REASON_CANCELLED_VOUCHER
from erpcore.tests.utils import ERPCoreTestCase


class IntegrationTestChequeVoidReason(ERPCoreTestCase):
	def test_every_default_reason_is_seeded(self):
		for reason_name, applies_to, _description in DEFAULT_VOID_REASONS:
			with self.subTest(reason=reason_name):
				self.assertEqual(
					frappe.db.get_value("Cheque Void Reason", reason_name, "applies_to"), applies_to
				)

	def test_the_cancelled_voucher_reason_exists(self):
		# cheque_utils stamps this one onto a leaf when its voucher is cancelled
		self.assertTrue(frappe.db.exists("Cheque Void Reason", VOID_REASON_CANCELLED_VOUCHER))

	def test_a_reason_is_named_after_itself(self):
		reason = frappe.get_doc(
			{"doctype": "Cheque Void Reason", "reason_name": "_Test Chewed By Dog", "applies_to": "Both"}
		).insert()

		self.assertEqual(reason.name, "_Test Chewed By Dog")

	def test_seeding_twice_does_not_duplicate(self):
		from erpcore.setup import seed_void_reasons

		before = frappe.db.count("Cheque Void Reason")
		seed_void_reasons()

		self.assertEqual(frappe.db.count("Cheque Void Reason"), before)
