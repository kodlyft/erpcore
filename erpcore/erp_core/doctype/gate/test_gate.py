# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe

from erpcore.tests.utils import TEST_COMPANY, ERPCoreTestCase


class IntegrationTestGate(ERPCoreTestCase):
	def test_gate_is_named_after_itself(self):
		gate = frappe.get_doc(
			{"doctype": "Gate", "gate_name": "_Test Dock 7", "company": TEST_COMPANY}
		).insert()

		self.assertEqual(gate.name, "_Test Dock 7")
		self.assertEqual(gate.gate_type, "Main")
		self.assertFalse(gate.disabled)

	def test_company_is_mandatory(self):
		gate = frappe.get_doc({"doctype": "Gate", "gate_name": "_Test Dock 8"})
		self.assertRaises(frappe.MandatoryError, gate.insert)
