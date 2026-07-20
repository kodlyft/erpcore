# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ChequeVoidReason(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		applies_to: DF.Literal["Both", "Outward", "Inward"]
		description: DF.SmallText | None
		disabled: DF.Check
		reason_name: DF.Data
	# end: auto-generated types

	pass
