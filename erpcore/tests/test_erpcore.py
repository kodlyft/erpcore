# Copyright (c) 2026, Kodlyft and Contributors
# See LICENSE

import unittest

import frappe


class TestERPCore(unittest.TestCase):
	def test_app_is_installed(self):
		self.assertIn("erpcore", frappe.get_installed_apps())

	def test_erpnext_is_available(self):
		"""erpcore declares erpnext in required_apps, so it must be installed alongside."""
		self.assertIn("erpnext", frappe.get_installed_apps())
