# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe

from erpcore.tests.utils import ERPCoreTestCase


class IntegrationTestChequeSettings(ERPCoreTestCase):
	def test_defaults_are_materialised_on_install(self):
		settings = frappe.get_cached_doc("Cheque Settings")

		self.assertEqual(settings.default_padding_length, 6)
		self.assertEqual(settings.max_leaves_per_book, 200)
		self.assertEqual(settings.background_generation_threshold, 200)
		self.assertEqual(settings.pdc_alert_days_before, 3)
		self.assertTrue(settings.require_void_reason)
		self.assertTrue(settings.auto_set_cleared_from_clearance_date)
		self.assertTrue(settings.reserve_leaf_on_draft)
		self.assertTrue(settings.warn_on_postdated_cheque)
		self.assertFalse(settings.allow_void_leaf_reuse)

	def test_settings_can_be_overridden_for_a_block(self):
		with self.change_settings("Cheque Settings", default_padding_length=8):
			self.assertEqual(frappe.db.get_single_value("Cheque Settings", "default_padding_length"), 8)

		self.assertEqual(frappe.db.get_single_value("Cheque Settings", "default_padding_length"), 6)
