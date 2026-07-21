# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe

from erpcore.setup import _target_exists, get_custom_fields, setup_custom_fields
from erpcore.tests.utils import ERPCoreTestCase


class TestSetup(ERPCoreTestCase):
	def test_voucher_custom_fields_are_installed(self):
		for doctype in ("Payment Entry", "Journal Entry"):
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": "cheque_leaf"}))
				self.assertTrue(
					frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": "cheque_leaf_status"})
				)

	def test_cheque_leaf_status_is_fetched_from_the_leaf(self):
		fetch_from = frappe.db.get_value(
			"Custom Field", {"dt": "Payment Entry", "fieldname": "cheque_leaf_status"}, "fetch_from"
		)
		self.assertEqual(fetch_from, "cheque_leaf.status")

	def test_fields_pointing_at_absent_doctypes_are_skipped(self):
		# Cheque Receipt is referenced by the Payment Entry field set but not shipped
		self.assertFalse(frappe.db.exists("DocType", "Cheque Receipt"))
		self.assertFalse(
			frappe.db.exists("Custom Field", {"dt": "Payment Entry", "fieldname": "cheque_receipt"})
		)

	def test_target_exists_only_guards_link_like_fields(self):
		self.assertTrue(_target_exists({"fieldtype": "Data", "options": "_Test Nothing"}))
		self.assertTrue(_target_exists({"fieldtype": "Link", "options": "Cheque Leaf"}))
		self.assertFalse(_target_exists({"fieldtype": "Link", "options": "_Test Nothing"}))
		self.assertFalse(_target_exists({"fieldtype": "Table", "options": "_Test Nothing"}))

	def test_installing_the_fields_twice_does_not_duplicate_them(self):
		setup_custom_fields()

		for doctype, definitions in get_custom_fields().items():
			for definition in definitions:
				if not _target_exists(definition):
					continue

				with self.subTest(doctype=doctype, fieldname=definition["fieldname"]):
					self.assertEqual(
						frappe.db.count(
							"Custom Field", {"dt": doctype, "fieldname": definition["fieldname"]}
						),
						1,
					)

	def test_leaf_indexes_exist(self):
		indexes = {row.Key_name for row in frappe.db.sql("show index from `tabCheque Leaf`", as_dict=True)}

		self.assertIn("cheque_book_number", indexes)
		self.assertIn("leaf_voucher_unique", indexes)

	def test_gate_pass_roles_have_desk_access(self):
		for role in ("Gate Pass User", "Gate Keeper", "Gate Pass Manager"):
			with self.subTest(role=role):
				self.assertTrue(frappe.db.get_value("Role", role, "desk_access"))
