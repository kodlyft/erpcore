# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe
from frappe.utils import add_days, nowdate

from erpcore.erp_core.doctype.gate_pass_return.gate_pass_return import make_gate_pass_return as map_return
from erpcore.tests.utils import TEST_UOM, ERPCoreTestCase, make_gate_pass, make_gate_pass_return


class IntegrationTestGatePassReturn(ERPCoreTestCase):
	def setUp(self):
		self.pass_ = make_gate_pass(returnable=1)
		self.row = self.pass_.items[0].name

	def test_return_needs_an_existing_gate_pass(self):
		doc = map_return(self.pass_.name)
		doc.return_against = "_Test Missing GP"

		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_a_draft_gate_pass_cannot_be_returned_against(self):
		draft = make_gate_pass(returnable=1, submit=False)
		self.assertRaises(frappe.ValidationError, map_return, draft.name)

	def test_a_non_returnable_gate_pass_cannot_be_returned_against(self):
		outright = make_gate_pass(returnable=0)
		self.assertRaises(frappe.ValidationError, map_return, outright.name)

	def test_a_fully_returned_gate_pass_is_closed_to_more_returns(self):
		make_gate_pass_return(self.pass_)

		# the mapper leaves nothing to return, and the controller refuses the save
		doc = map_return(self.pass_.name)
		self.assertEqual(len(doc.items), 0)
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_negative_quantity_is_refused(self):
		self.assertRaises(
			frappe.ValidationError, make_gate_pass_return, self.pass_, {self.row: -1}, submit=False
		)

	def test_a_return_of_nothing_is_refused(self):
		self.assertRaises(
			frappe.ValidationError, make_gate_pass_return, self.pass_, {self.row: 0}, submit=False
		)

	def test_over_return_is_refused(self):
		self.assertRaises(
			frappe.ValidationError, make_gate_pass_return, self.pass_, {self.row: 11}, submit=False
		)

	def test_pending_quantity_excludes_this_documents_own_effect(self):
		make_gate_pass_return(self.pass_, {self.row: 4})

		second = make_gate_pass_return(self.pass_, {self.row: 6}, submit=False)
		self.assertEqual(second.items[0].pending_qty, 6)

		self.assertRaises(
			frappe.ValidationError, make_gate_pass_return, self.pass_, {self.row: 7}, submit=False
		)

	def test_totals(self):
		doc = make_gate_pass_return(self.pass_, {self.row: 4}, submit=False)
		self.assertEqual(doc.total_returned_qty, 4)

	def test_a_partial_return_updates_the_source(self):
		make_gate_pass_return(self.pass_, {self.row: 4})
		self.pass_.reload()

		self.assertEqual(self.pass_.return_status, "Partly Returned")
		self.assertEqual(self.pass_.per_returned, 40)
		self.assertEqual(self.pass_.items[0].returned_qty, 4)
		self.assertEqual(self.pass_.items[0].pending_qty, 6)
		self.assertEqual(str(self.pass_.actual_return_date), nowdate())

	def test_a_full_return_closes_the_source(self):
		make_gate_pass_return(self.pass_, {self.row: 10})
		self.pass_.reload()

		self.assertEqual(self.pass_.return_status, "Fully Returned")
		self.assertEqual(self.pass_.per_returned, 100)
		self.assertEqual(self.pass_.status, "Returned")

	def test_returns_accumulate(self):
		make_gate_pass_return(self.pass_, {self.row: 4})
		make_gate_pass_return(self.pass_, {self.row: 6})
		self.pass_.reload()

		self.assertEqual(self.pass_.per_returned, 100)
		self.assertEqual(self.pass_.return_status, "Fully Returned")

	def test_cancelling_a_return_reverses_it(self):
		returned = make_gate_pass_return(self.pass_, {self.row: 4})
		returned.cancel()
		self.pass_.reload()

		self.assertEqual(self.pass_.return_status, "Not Returned")
		self.assertEqual(self.pass_.per_returned, 0)
		self.assertEqual(self.pass_.items[0].returned_qty, 0)

	def test_status_tracks_the_docstatus(self):
		doc = make_gate_pass_return(self.pass_, {self.row: 4}, submit=False)
		self.assertEqual(doc.status, "Draft")

		doc.submit()
		self.assertEqual(doc.status, "Submitted")

		doc.cancel()
		self.assertEqual(doc.status, "Cancelled")

	def test_a_gate_pass_with_returns_cannot_be_cancelled(self):
		make_gate_pass_return(self.pass_, {self.row: 4})
		self.pass_.reload()

		self.assertRaises(frappe.ValidationError, self.pass_.cancel)

	def test_mapper_prefills_what_is_still_outside(self):
		doc = map_return(self.pass_.name)

		self.assertEqual(doc.return_against, self.pass_.name)
		self.assertEqual(len(doc.items), 1)
		self.assertEqual(doc.items[0].gate_pass_item, self.row)
		self.assertEqual(doc.items[0].original_qty, 10)
		self.assertEqual(doc.items[0].pending_qty, 10)
		self.assertEqual(doc.items[0].returned_qty, 10)

	def test_mapper_skips_rows_already_back(self):
		two_rows = make_gate_pass(
			returnable=1,
			items=[
				{"is_stock_item": 0, "item_description": "_Test A", "qty": 2, "uom": TEST_UOM},
				{"is_stock_item": 0, "item_description": "_Test B", "qty": 3, "uom": TEST_UOM},
			],
		)
		first_row = two_rows.items[0].name
		make_gate_pass_return(two_rows, {first_row: 2, two_rows.items[1].name: 0})

		doc = map_return(two_rows.name)

		self.assertEqual([row.gate_pass_item for row in doc.items], [two_rows.items[1].name])

	def test_return_carries_the_source_posting_date_forward(self):
		older = make_gate_pass(
			returnable=1,
			posting_date=add_days(nowdate(), -3),
			expected_return_date=add_days(nowdate(), 4),
		)
		doc = map_return(older.name)

		self.assertEqual(str(doc.posting_date), nowdate())
