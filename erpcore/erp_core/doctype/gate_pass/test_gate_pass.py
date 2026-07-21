# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe
from frappe.utils import add_days, nowdate

from erpcore.erp_core.doctype.gate_pass.gate_pass import get_source_items
from erpcore.erp_core.gate_pass_base import gate_pass_query_conditions
from erpcore.tests.utils import (
	GUARD_USER,
	PLAIN_USER,
	TEST_COMPANY,
	TEST_ITEM,
	TEST_UOM,
	ERPCoreTestCase,
	make_gate_pass,
)


class IntegrationTestGatePass(ERPCoreTestCase):
	def test_pass_type_matrix(self):
		cases = [
			("Inward", 0, "IGP", "GP-IGP-"),
			("Inward", 1, "RGP", "GP-RGP-"),
			("Outward", 0, "NRGP", "GP-NRGP-"),
			("Outward", 1, "RGP", "GP-RGP-"),
		]

		for direction, returnable, pass_type, series in cases:
			with self.subTest(direction=direction, returnable=returnable):
				doc = make_gate_pass(direction=direction, returnable=returnable, submit=False)
				self.assertEqual(doc.pass_type, pass_type)
				self.assertTrue(doc.name.startswith(series))

	def test_party_name_is_fetched_from_the_party(self):
		doc = make_gate_pass(party_type="Supplier", party="_Test Supplier", submit=False)
		self.assertEqual(doc.party_name, "_Test Supplier")

	def test_unsupported_party_type_is_refused(self):
		self.assertRaises(frappe.ValidationError, make_gate_pass, party_type="Shareholder", submit=False)

	def test_party_is_dropped_without_a_party_type(self):
		doc = make_gate_pass(party_type="Supplier", party="_Test Supplier", submit=False)
		doc.party_type = None
		doc.set_party()

		self.assertIsNone(doc.party)

	def test_a_pass_needs_at_least_one_item(self):
		self.assertRaises(frappe.ValidationError, make_gate_pass, items=[], submit=False)

	def test_quantity_must_be_positive(self):
		self.assertRaises(
			frappe.ValidationError,
			make_gate_pass,
			items=[{"is_stock_item": 0, "item_description": "_Test Crate", "qty": 0, "uom": TEST_UOM}],
			submit=False,
		)

	def test_a_non_stock_row_needs_a_description(self):
		self.assertRaises(
			frappe.ValidationError,
			make_gate_pass,
			items=[{"is_stock_item": 0, "qty": 1, "uom": TEST_UOM}],
			submit=False,
		)

	def test_the_same_source_row_cannot_be_listed_twice(self):
		row = {
			"is_stock_item": 1,
			"item_code": TEST_ITEM,
			"qty": 1,
			"uom": TEST_UOM,
			"reference_doctype": "Purchase Order",
			"reference_name": "_Test PO",
			"reference_item_row": "abc123",
		}

		self.assertRaises(frappe.ValidationError, make_gate_pass, items=[row, dict(row)], submit=False)

	def test_free_text_rows_may_repeat(self):
		row = {"is_stock_item": 0, "item_description": "_Test Crate", "qty": 1, "uom": TEST_UOM}
		doc = make_gate_pass(items=[dict(row), dict(row)], submit=False)

		self.assertEqual(doc.total_items, 2)

	def test_totals_and_row_amounts(self):
		doc = make_gate_pass(
			items=[
				{
					"is_stock_item": 0,
					"item_description": "_Test Crate",
					"qty": 3,
					"uom": TEST_UOM,
					"rate": 100,
				},
				{
					"is_stock_item": 0,
					"item_description": "_Test Pallet",
					"qty": 2,
					"uom": TEST_UOM,
					"rate": 50,
				},
			],
			submit=False,
		)

		self.assertEqual(doc.total_items, 2)
		self.assertEqual(doc.total_qty, 5)
		self.assertEqual(doc.items[0].amount, 300)
		self.assertEqual(doc.items[1].amount, 100)

	def test_non_returnable_rows_carry_no_return_quantities(self):
		doc = make_gate_pass(returnable=0, submit=False)

		self.assertEqual(doc.items[0].returned_qty, 0)
		self.assertEqual(doc.items[0].pending_qty, 0)
		self.assertEqual(doc.return_status, "Not Returned")
		self.assertIsNone(doc.expected_return_date)

	def test_returnable_rows_start_fully_pending(self):
		doc = make_gate_pass(returnable=1, submit=False)
		self.assertEqual(doc.items[0].pending_qty, doc.items[0].qty)

	def test_expected_return_date_defaults_from_settings(self):
		with self.change_settings("Gate Pass Settings", default_return_days=10):
			doc = make_gate_pass(returnable=1, submit=False)

		self.assertEqual(str(doc.expected_return_date), add_days(doc.posting_date, 10))

	def test_return_date_cannot_precede_the_pass(self):
		self.assertRaises(
			frappe.ValidationError,
			make_gate_pass,
			returnable=1,
			expected_return_date=add_days(nowdate(), -1),
			submit=False,
		)

	def test_draft_status(self):
		self.assertEqual(make_gate_pass(submit=False).status, "Draft")

	def test_submitted_pass_is_approved(self):
		doc = make_gate_pass()
		self.assertEqual(doc.status, "Approved")
		self.assertEqual(doc.approved_by, frappe.session.user)

	def test_verified_pass_sits_at_the_gate(self):
		doc = make_gate_pass()
		doc.verify(remarks="counted 10 crates")
		doc.reload()

		self.assertEqual(doc.status, "At Gate")
		self.assertTrue(doc.guard_verified)
		self.assertEqual(doc.guard_remarks, "counted 10 crates")

	def test_outward_pass_exits(self):
		doc = make_gate_pass(direction="Outward")
		doc.verify()
		self.assertEqual(doc.check_out(), "Exited")

	def test_inward_pass_is_received(self):
		doc = make_gate_pass(direction="Inward")
		doc.check_in()
		doc.reload()
		doc.verify()
		self.assertEqual(doc.check_out(), "Received")

	def test_overdue_pass(self):
		doc = make_gate_pass(
			returnable=1,
			posting_date=add_days(nowdate(), -10),
			expected_return_date=add_days(nowdate(), -2),
		)
		doc.verify()
		self.assertEqual(doc.check_out(), "Overdue")

	def test_check_in_is_recorded_once(self):
		doc = make_gate_pass(direction="Inward")
		doc.check_in()
		doc.reload()

		self.assertTrue(doc.checked_in_at)
		self.assertEqual(doc.checked_by, frappe.session.user)
		self.assertRaises(frappe.ValidationError, doc.check_in)

	def test_check_out_is_recorded_once(self):
		doc = make_gate_pass()
		doc.verify()
		doc.reload()
		doc.check_out()
		doc.reload()

		self.assertRaises(frappe.ValidationError, doc.check_out)

	def test_check_out_needs_verification(self):
		doc = make_gate_pass()
		self.assertRaises(frappe.ValidationError, doc.check_out)

	def test_check_out_without_verification_when_not_required(self):
		doc = make_gate_pass()

		with self.change_settings("Gate Pass Settings", require_guard_verification=0):
			self.assertEqual(doc.check_out(), "Exited")

	def test_a_draft_cannot_be_processed_at_the_gate(self):
		doc = make_gate_pass(submit=False)
		self.assertRaises(frappe.ValidationError, doc.verify)

	def test_only_a_guard_can_record_movements(self):
		doc = make_gate_pass()

		with self.set_user(PLAIN_USER):
			self.assertRaises(frappe.PermissionError, doc.verify)

	def test_a_gate_keeper_can_record_movements(self):
		doc = make_gate_pass()

		with self.set_user(GUARD_USER):
			doc.verify()

		self.assertTrue(frappe.db.get_value("Gate Pass", doc.name, "guard_verified"))

	def test_guard_can_only_write_guard_fields(self):
		doc = make_gate_pass()
		self.assertRaises(frappe.ValidationError, doc.guard_set, {"purpose": "tampering"})

	def test_close_a_finished_pass(self):
		doc = make_gate_pass()
		self.assertEqual(doc.close(), "Closed")

	def test_a_returnable_pass_cannot_be_closed_early(self):
		doc = make_gate_pass(returnable=1)
		self.assertRaises(frappe.ValidationError, doc.close)

	def test_cancel_sets_the_status(self):
		doc = make_gate_pass()
		doc.cancel()
		doc.reload()

		self.assertEqual(doc.status, "Cancelled")

	def test_source_items_from_an_unsupported_doctype(self):
		self.assertRaises(frappe.ValidationError, get_source_items, "Sales Invoice", ["_Test SI"])

	def test_source_items_from_a_purchase_order(self):
		from erpnext.buying.doctype.purchase_order.test_purchase_order import create_purchase_order

		order = create_purchase_order(item_code=TEST_ITEM, qty=4, rate=25)
		rows = get_source_items("Purchase Order", [order.name])

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["item_code"], TEST_ITEM)
		self.assertEqual(rows[0]["qty"], 4)
		self.assertEqual(rows[0]["amount"], 100)
		self.assertEqual(rows[0]["reference_doctype"], "Purchase Order")
		self.assertEqual(rows[0]["reference_name"], order.name)
		self.assertEqual(rows[0]["reference_item_row"], order.items[0].name)

	def test_source_items_of_nothing(self):
		self.assertEqual(get_source_items("Purchase Order", []), [])

	def test_managers_see_every_company(self):
		self.assertIsNone(gate_pass_query_conditions(frappe.session.user))

	def test_users_are_limited_to_their_permitted_companies(self):
		permission = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": PLAIN_USER,
				"allow": "Company",
				"for_value": TEST_COMPANY,
			}
		).insert(ignore_permissions=True)

		try:
			condition = gate_pass_query_conditions(PLAIN_USER)
			self.assertIn("`tabGate Pass`.`company` in", condition)
			self.assertIn(TEST_COMPANY, condition)
		finally:
			permission.delete()
