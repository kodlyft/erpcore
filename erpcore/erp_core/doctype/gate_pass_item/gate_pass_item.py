# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class GatePassItem(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amount: DF.Currency
		batch_no: DF.Link | None
		description: DF.SmallText | None
		expected_return_date: DF.Date | None
		is_stock_item: DF.Check
		item_code: DF.Link | None
		item_description: DF.Data | None
		item_name: DF.Data | None
		item_size: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		pending_qty: DF.Float
		qty: DF.Float
		rate: DF.Currency
		reference_doctype: DF.Link | None
		reference_item_row: DF.Data | None
		reference_name: DF.DynamicLink | None
		remarks: DF.Data | None
		returned_qty: DF.Float
		serial_no: DF.SmallText | None
		source_qty: DF.Float
		uom: DF.Link
	# end: auto-generated types

	pass
