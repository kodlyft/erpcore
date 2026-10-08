# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MonthlyCloseCheckResult(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amount: DF.Float
		check_id: DF.Data | None
		check_label: DF.Data | None
		check_version: DF.Int
		count: DF.Int
		evaluated_at: DF.Datetime | None
		finding_signature: DF.Data | None
		identity_count: DF.Int
		message: DF.SmallText | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		route: DF.Data | None
		samples: DF.Code | None
		severity: DF.Data | None
		status: DF.Literal["Passed", "Warning", "Blocker", "Not Applicable", "Error"]
		tolerance: DF.Float
		waivable: DF.Check
	# end: auto-generated types

	pass
