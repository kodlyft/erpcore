# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt


class GatePassReturn(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpcore.erp_core.doctype.gate_pass_return_item.gate_pass_return_item import GatePassReturnItem

		amended_from: DF.Link | None
		carrying_by: DF.Data
		checked_in_at: DF.Datetime | None
		company: DF.Link
		driver_name: DF.Data | None
		gate: DF.Link | None
		guard_remarks: DF.SmallText | None
		guard_verified: DF.Check
		items: DF.Table[GatePassReturnItem]
		naming_series: DF.Literal["GPR-.YYYY.-.#####"]
		party: DF.DynamicLink | None
		party_name: DF.Data | None
		party_type: DF.Link | None
		posting_date: DF.Date
		posting_time: DF.Time
		received_by: DF.Link | None
		remarks: DF.SmallText | None
		return_against: DF.Link
		status: DF.Literal["Draft", "Submitted", "Cancelled"]
		total_returned_qty: DF.Float
		vehicle_no: DF.Data | None
	# end: auto-generated types

	def validate(self):
		self.validate_source()
		self.validate_quantities()
		self.set_totals()
		self.status = {0: "Draft", 1: "Submitted", 2: "Cancelled"}[self.docstatus]

	def validate_source(self):
		source = frappe.db.get_value(
			"Gate Pass",
			self.return_against,
			["docstatus", "returnable", "return_status", "company"],
			as_dict=True,
		)

		if not source:
			frappe.throw(_("Gate Pass {0} does not exist.").format(self.return_against))

		if source.docstatus != 1:
			frappe.throw(_("Gate Pass {0} is not submitted.").format(frappe.bold(self.return_against)))

		if not source.returnable:
			frappe.throw(
				_("Gate Pass {0} is not returnable, so nothing can be returned against it.").format(
					frappe.bold(self.return_against)
				)
			)

		if source.return_status == "Fully Returned":
			frappe.throw(
				_("Gate Pass {0} has already been fully returned.").format(frappe.bold(self.return_against))
			)

	def validate_quantities(self):
		if not self.items:
			frappe.throw(_("List at least one item being returned."))

		pending = self.get_pending_quantities()
		returning_any = False

		for row in self.items:
			if flt(row.returned_qty) < 0:
				frappe.throw(_("Row {0}: Returning Now cannot be negative.").format(row.idx))

			if flt(row.returned_qty) == 0:
				continue

			returning_any = True
			allowed = flt(pending.get(row.gate_pass_item, 0))

			if flt(row.returned_qty) > allowed:
				frappe.throw(
					_("Row {0}: cannot return {1} of {2} when only {3} is still outside.").format(
						row.idx,
						frappe.bold(flt(row.returned_qty)),
						frappe.bold(row.item_code or row.item_name),
						frappe.bold(allowed),
					),
					title=_("Over Return"),
				)

			row.pending_qty = allowed

		if not returning_any:
			frappe.throw(_("Enter a quantity against at least one item."))

	def get_pending_quantities(self):
		"""Outstanding qty per source row, excluding this document's own effect."""
		rows = frappe.get_all(
			"Gate Pass Item",
			filters={"parent": self.return_against, "parenttype": "Gate Pass"},
			fields=["name", "qty"],
		)
		originals = {row.name: flt(row.qty) for row in rows}

		other_returns = frappe.get_all(
			"Gate Pass Return",
			filters={
				"return_against": self.return_against,
				"docstatus": 1,
				"name": ["!=", self.name],
			},
			pluck="name",
		)

		returned = {}
		if other_returns:
			for row in frappe.get_all(
				"Gate Pass Return Item",
				filters={"parent": ["in", other_returns], "parenttype": "Gate Pass Return"},
				fields=["gate_pass_item", "returned_qty"],
			):
				returned[row.gate_pass_item] = returned.get(row.gate_pass_item, 0) + flt(row.returned_qty)

		return {name: qty - flt(returned.get(name, 0)) for name, qty in originals.items()}

	def set_totals(self):
		self.total_returned_qty = sum(flt(row.returned_qty) for row in self.items)

	def on_submit(self):
		self.db_set("status", "Submitted")
		self.update_source()

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		self.update_source()

	def update_source(self):
		frappe.get_doc("Gate Pass", self.return_against).update_return_status()


@frappe.whitelist()
def make_gate_pass_return(source_name: str, target_doc: str | None = None):
	"""Build a return pre-filled with whatever is still outside."""

	def set_missing_values(source, target):
		target.posting_date = frappe.utils.nowdate()
		target.posting_time = frappe.utils.nowtime()
		target.run_method("set_missing_values")

	def update_item(source_row, target_row, source_parent):
		returned = flt(source_row.returned_qty)
		target_row.gate_pass_item = source_row.name
		target_row.original_qty = flt(source_row.qty)
		target_row.previously_returned_qty = returned
		target_row.pending_qty = flt(source_row.qty) - returned
		target_row.returned_qty = target_row.pending_qty

	def item_still_outside(doc):
		return flt(doc.qty) - flt(doc.returned_qty) > 0

	return get_mapped_doc(
		"Gate Pass",
		source_name,
		{
			"Gate Pass": {
				"doctype": "Gate Pass Return",
				"field_map": {"name": "return_against"},
				"validation": {"docstatus": ["=", 1], "returnable": ["=", 1]},
			},
			"Gate Pass Item": {
				"doctype": "Gate Pass Return Item",
				"field_map": {
					"item_code": "item_code",
					"item_name": "item_name",
					"description": "description",
					"uom": "uom",
				},
				"postprocess": update_item,
				"condition": item_still_outside,
			},
		},
		target_doc,
		set_missing_values,
	)
