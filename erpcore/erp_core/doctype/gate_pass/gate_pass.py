# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime, nowdate

from erpcore.erp_core.gate_pass_base import GatePassBase

ALLOWED_PARTY_TYPES = ("Supplier", "Customer", "Employee")

PASS_TYPES = {
	("Inward", 0): ("IGP", "GP-IGP-.YYYY.-.#####"),
	("Inward", 1): ("RGP", "GP-RGP-.YYYY.-.#####"),
	("Outward", 0): ("NRGP", "GP-NRGP-.YYYY.-.#####"),
	("Outward", 1): ("RGP", "GP-RGP-.YYYY.-.#####"),
}


class GatePass(GatePassBase, Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpcore.erp_core.doctype.gate_pass_item.gate_pass_item import GatePassItem

		actual_return_date: DF.Date | None
		amended_from: DF.Link | None
		approved_by: DF.Link | None
		carrying_by: DF.Data
		challan_no: DF.Data | None
		checked_by: DF.Link | None
		checked_by_user: DF.Link | None
		checked_in_at: DF.Datetime | None
		checked_out_at: DF.Datetime | None
		company: DF.Link
		department: DF.Link | None
		direction: DF.Literal["Inward", "Outward"]
		driver: DF.Link | None
		driver_contact: DF.Data | None
		driver_name: DF.Data | None
		expected_return_date: DF.Date | None
		for_machine: DF.Link | None
		gate: DF.Link | None
		guard_remarks: DF.SmallText | None
		guard_verified: DF.Check
		items: DF.Table[GatePassItem]
		lr_date: DF.Date | None
		lr_no: DF.Data | None
		naming_series: DF.Literal[
			"GP-IGP-.YYYY.-.#####", "GP-OGP-.YYYY.-.#####", "GP-RGP-.YYYY.-.#####", "GP-NRGP-.YYYY.-.#####"
		]
		party: DF.DynamicLink | None
		party_name: DF.Data
		party_type: DF.Link | None
		pass_type: DF.Data | None
		per_returned: DF.Percent
		posting_date: DF.Date
		posting_time: DF.Time
		prepared_by: DF.Link | None
		purpose: DF.SmallText
		remarks: DF.SmallText | None
		return_status: DF.Literal["Not Returned", "Partly Returned", "Fully Returned"]
		returnable: DF.Check
		status: DF.Literal[
			"Draft",
			"Pending Approval",
			"Approved",
			"At Gate",
			"Exited",
			"Received",
			"Partly Returned",
			"Returned",
			"Overdue",
			"Closed",
			"Cancelled",
			"Rejected",
		]
		total_items: DF.Int
		total_qty: DF.Float
		transporter: DF.Link | None
		vehicle: DF.Link | None
		vehicle_no: DF.Data | None
	# end: auto-generated types

	def before_naming(self):
		if self.is_new():
			self.naming_series = self.get_pass_config()[1]

	def get_pass_config(self):
		return PASS_TYPES[(self.direction, 1 if self.returnable else 0)]

	def validate(self):
		self.set_pass_type()
		self.set_party()
		self.set_vehicle_details()
		self.validate_items()
		self.set_totals()
		self.set_return_defaults()
		self.set_status()

	def set_pass_type(self):
		self.pass_type = self.get_pass_config()[0]

	def set_party(self):
		"""Validate the party type and fill in the display name."""
		if not self.party_type:
			self.party = None
			return

		if self.party_type not in ALLOWED_PARTY_TYPES:
			frappe.throw(
				_("Party Type must be one of {0}.").format(", ".join(ALLOWED_PARTY_TYPES)),
			)

		if not self.party:
			return

		title_field = frappe.get_meta(self.party_type).get_title_field()
		self.party_name = frappe.db.get_value(self.party_type, self.party, title_field)

	def set_vehicle_details(self):
		if self.vehicle and not self.vehicle_no:
			self.vehicle_no = self.vehicle

		if self.driver and not self.driver_name:
			self.driver_name = frappe.db.get_value("Driver", self.driver, "full_name")

	def validate_items(self):
		if not self.items:
			frappe.throw(_("A gate pass must list at least one item."))

		seen = set()
		for row in self.items:
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Quantity must be greater than zero.").format(row.idx))

			if not row.is_stock_item and not row.item_description:
				frappe.throw(
					_("Row {0}: Describe the item, or tick Stock Item and pick one.").format(row.idx)
				)

			key = (row.item_code, row.reference_name, row.reference_item_row)
			if key != (None, None, None) and key in seen:
				frappe.throw(
					_("Row {0}: {1} from {2} is listed twice.").format(
						row.idx, frappe.bold(row.item_code), frappe.bold(row.reference_name)
					)
				)
			seen.add(key)

			row.amount = flt(row.qty) * flt(row.rate)

			if self.returnable:
				row.pending_qty = flt(row.qty) - flt(row.returned_qty)
			else:
				row.returned_qty = 0
				row.pending_qty = 0

	def set_totals(self):
		self.total_items = len(self.items)
		self.total_qty = sum(flt(row.qty) for row in self.items)

	def set_return_defaults(self):
		if not self.returnable:
			self.expected_return_date = None
			self.return_status = "Not Returned"
			self.per_returned = 0
			return

		if not self.expected_return_date:
			days = frappe.db.get_single_value("Gate Pass Settings", "default_return_days") or 7
			self.expected_return_date = frappe.utils.add_days(self.posting_date or nowdate(), days)

		if getdate(self.expected_return_date) < getdate(self.posting_date or nowdate()):
			frappe.throw(_("Expected Return Date cannot be before the gate pass date."))

	def set_status(self, update=False):
		"""Derive status from docstatus, gate timestamps and return progress."""
		if self.docstatus == 2:
			status = "Cancelled"
		elif self.docstatus == 0:
			status = self.status if self.status in ("Draft", "Pending Approval", "Rejected") else "Draft"
		else:
			status = self.compute_submitted_status()

		if update:
			self.db_set("status", status)
		else:
			self.status = status

		return status

	def compute_submitted_status(self):
		if self.returnable and self.return_status == "Fully Returned":
			return "Returned"

		if self.status == "Closed":
			return "Closed"

		if not self.checked_out_at and not self.checked_in_at:
			return "At Gate" if self.guard_verified else "Approved"

		if not self.returnable:
			return "Exited" if self.direction == "Outward" else "Received"

		if self.expected_return_date and getdate(self.expected_return_date) < getdate(nowdate()):
			return "Overdue"

		return "Partly Returned" if self.return_status == "Partly Returned" else "Exited"

	def on_submit(self):
		self.db_set({"prepared_by": self.owner, "approved_by": frappe.session.user})
		self.set_status(update=True)

	def on_cancel(self):
		self.check_no_returns()
		self.set_status(update=True)

	def check_no_returns(self):
		returns = frappe.db.count("Gate Pass Return", {"return_against": self.name, "docstatus": 1})
		if returns:
			frappe.throw(
				_("Cannot cancel: {0} submitted returns exist against this gate pass.").format(
					frappe.bold(returns)
				),
				title=_("Returns Recorded"),
			)

	@frappe.whitelist()
	def check_in(self):
		"""Record material arriving at the gate."""
		self.require_guard_role()
		self.require_submitted()

		if self.checked_in_at:
			frappe.throw(_("Already checked in at {0}.").format(self.checked_in_at))

		self.guard_set({"checked_in_at": now_datetime(), "checked_by": frappe.session.user})
		self.reload()
		self.set_status(update=True)
		return self.status

	@frappe.whitelist()
	def check_out(self):
		"""Record material leaving the gate."""
		self.require_guard_role()
		self.require_submitted()

		if self.checked_out_at:
			frappe.throw(_("Already checked out at {0}.").format(self.checked_out_at))

		settings = frappe.get_cached_doc("Gate Pass Settings")
		if settings.require_guard_verification and not self.guard_verified:
			frappe.throw(
				_("Verify the material before releasing it."),
				title=_("Verification Required"),
			)

		self.guard_set({"checked_out_at": now_datetime(), "checked_by": frappe.session.user})
		self.reload()
		self.set_status(update=True)
		return self.status

	@frappe.whitelist()
	def verify(self, remarks: str | None = None):
		"""Guard confirms the physical material matches the paperwork."""
		self.require_guard_role()
		self.require_submitted()

		values = {"guard_verified": 1, "checked_by": frappe.session.user}
		if remarks:
			values["guard_remarks"] = remarks

		self.guard_set(values)
		self.reload()
		self.set_status(update=True)
		return self.status

	@frappe.whitelist()
	def close(self):
		"""Manually close a pass that needs no further tracking."""
		self.require_submitted()

		if self.returnable and self.return_status != "Fully Returned":
			frappe.throw(
				_("This pass is returnable and {0}% has come back. Record the returns first.").format(
					flt(self.per_returned, 2)
				)
			)

		self.db_set("status", "Closed")
		return self.status

	def update_return_status(self):
		"""Recompute return progress from every submitted return."""
		returned = self.get_returned_quantities()

		total_qty = 0.0
		total_returned = 0.0

		for row in self.items:
			row_returned = flt(returned.get(row.name, 0))
			frappe.db.set_value(
				"Gate Pass Item",
				row.name,
				{"returned_qty": row_returned, "pending_qty": flt(row.qty) - row_returned},
				update_modified=False,
			)
			total_qty += flt(row.qty)
			total_returned += row_returned

		per_returned = (total_returned / total_qty * 100) if total_qty else 0

		if per_returned <= 0:
			return_status = "Not Returned"
		elif per_returned >= 99.99:
			return_status = "Fully Returned"
		else:
			return_status = "Partly Returned"

		self.db_set(
			{
				"per_returned": flt(per_returned, 2),
				"return_status": return_status,
				"actual_return_date": self.get_last_return_date(),
			}
		)
		self.reload()
		self.set_status(update=True)

	def get_returned_quantities(self):
		"""Returned qty per source row, summed over every submitted return."""
		return_names = frappe.get_all(
			"Gate Pass Return",
			filters={"return_against": self.name, "docstatus": 1},
			pluck="name",
		)
		if not return_names:
			return {}

		totals = {}
		for row in frappe.get_all(
			"Gate Pass Return Item",
			filters={"parent": ["in", return_names], "parenttype": "Gate Pass Return"},
			fields=["gate_pass_item", "returned_qty"],
		):
			totals[row.gate_pass_item] = totals.get(row.gate_pass_item, 0) + flt(row.returned_qty)

		return totals

	def get_last_return_date(self):
		return frappe.db.get_value(
			"Gate Pass Return",
			{"return_against": self.name, "docstatus": 1},
			"posting_date",
			order_by="posting_date desc",
		)


SOURCE_ITEM_TABLES = {
	"Purchase Order": ("Purchase Order Item", "qty"),
	"Delivery Note": ("Delivery Note Item", "qty"),
	"Stock Entry": ("Stock Entry Detail", "qty"),
}


@frappe.whitelist()
def get_source_items(source_doctype: str, source_names: str | list, child_doctype: str | None = None):
	"""Build gate pass item rows from one or more source documents."""
	if source_doctype not in SOURCE_ITEM_TABLES:
		frappe.throw(_("Cannot pull items from {0}.").format(source_doctype))

	frappe.has_permission(source_doctype, "read", throw=True)

	child_table, qty_field = SOURCE_ITEM_TABLES[source_doctype]
	names = frappe.parse_json(source_names) if isinstance(source_names, str) else source_names
	if not names:
		return []

	rows = frappe.get_all(
		child_table,
		filters={"parent": ["in", names], "parenttype": source_doctype},
		fields=["name", "parent", "item_code", "item_name", "description", "uom", qty_field, "rate"],
		order_by="parent, idx",
	)

	return [
		{
			"is_stock_item": 1,
			"item_code": row.item_code,
			"item_name": row.item_name,
			"description": row.description,
			"uom": row.uom,
			"qty": flt(row.get(qty_field)),
			"source_qty": flt(row.get(qty_field)),
			"rate": flt(row.rate),
			"amount": flt(row.get(qty_field)) * flt(row.rate),
			"reference_doctype": source_doctype,
			"reference_name": row.parent,
			"reference_item_row": row.name,
		}
		for row in rows
	]
