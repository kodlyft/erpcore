# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MonthlyCloseTemplateTask(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		assigned_role: DF.Link | None
		category: DF.Literal[
			"Preparation",
			"Reconciliation",
			"Accruals",
			"Tax",
			"Payroll",
			"Intercompany",
			"Review",
			"Approval",
			"Other",
		]
		depends_on: DF.Data | None
		description: DF.SmallText | None
		due_offset_days: DF.Int
		evidence_required: DF.Check
		is_mandatory: DF.Check
		manual_certification: DF.Check
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		task_key: DF.Data
		title: DF.Data
	# end: auto-generated types

	pass
