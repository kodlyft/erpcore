# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class Gate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link
		disabled: DF.Check
		gate_name: DF.Data
		gate_type: DF.Literal["Main", "Material", "Staff", "Visitor"]
		location: DF.Data | None
	# end: auto-generated types

	pass
