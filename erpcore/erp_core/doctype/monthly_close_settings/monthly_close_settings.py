# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MonthlyCloseSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		default_template: DF.Link | None
		enabled: DF.Check
		help_html: DF.HTML | None
		max_sample_rows: DF.Int
	# end: auto-generated types

	pass
