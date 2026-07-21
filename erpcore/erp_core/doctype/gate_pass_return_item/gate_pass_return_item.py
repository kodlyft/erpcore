# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class GatePassReturnItem(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		gate_pass_item: DF.Data | None
		item_code: DF.Link | None
		item_condition: DF.Literal["Good", "Damaged", "Scrap", "Short"]
		item_name: DF.Data | None
		original_qty: DF.Float
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		pending_qty: DF.Float
		previously_returned_qty: DF.Float
		remarks: DF.Data | None
		returned_qty: DF.Float
		uom: DF.Link | None
	# end: auto-generated types

	pass
