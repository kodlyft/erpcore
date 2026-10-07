# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MonthlyCloseSnapshot(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		app_versions: DF.Code | None
		company: DF.Link | None
		file: DF.Attach | None
		filters_json: DF.Code | None
		from_date: DF.Date | None
		generated_at: DF.Datetime | None
		monthly_close: DF.Link | None
		report_name: DF.Data | None
		revision: DF.Int
		row_count: DF.Int
		scope: DF.Data | None
		sha256: DF.Data | None
		status: DF.Literal["Original", "Superseded"]
		to_date: DF.Date | None
	# end: auto-generated types

	def validate(self):
		if not self.is_new() or not self.flags.erpcore_service:
			frappe.throw(_("Snapshots are written once by the close service."), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(
			_("Snapshots are part of the close evidence and cannot be deleted."), frappe.PermissionError
		)
