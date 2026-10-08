# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe

from erpcore.tests.utils import ERPCoreTestCase


class IntegrationTestGatePassSettings(ERPCoreTestCase):
	def test_defaults_are_materialised_on_install(self):
		settings = frappe.get_cached_doc("Gate Pass Settings")

		self.assertEqual(settings.default_return_days, 7)
		self.assertEqual(settings.overdue_alert_days, 1)
		self.assertEqual(settings.visitor_badge_prefix, "V")
		self.assertTrue(settings.require_guard_verification)
		self.assertTrue(settings.enable_approval_workflow)
		self.assertFalse(settings.allow_exit_without_approval)
		self.assertFalse(settings.allow_purchase_receipt_creation)

	def test_notify_roles_is_a_role_table(self):
		settings = frappe.get_doc("Gate Pass Settings")
		settings.append("notify_roles", {"role": "Gate Pass Manager"})
		settings.save()

		self.assertEqual(
			[row.role for row in frappe.get_doc("Gate Pass Settings").notify_roles],
			["Gate Pass Manager"],
		)
