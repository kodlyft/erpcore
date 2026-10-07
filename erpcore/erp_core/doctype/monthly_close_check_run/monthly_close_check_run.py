# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpcore.erp_core.monthly_close.constants import TRANSITION_FLAG


class MonthlyCloseCheckRun(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpcore.erp_core.doctype.monthly_close_check_result.monthly_close_check_result import (
			MonthlyCloseCheckResult,
		)

		attempts: DF.Int
		blockers: DF.Int
		company: DF.Link | None
		error: DF.SmallText | None
		errors: DF.Int
		finished_at: DF.Datetime | None
		fingerprint: DF.Data | None
		fingerprint_components: DF.Code | None
		job_id: DF.Data | None
		monthly_close: DF.Link | None
		policy_version: DF.Int
		purpose: DF.Literal["Background", "Final"]
		requested_by: DF.Link | None
		results: DF.Table[MonthlyCloseCheckResult]
		revision: DF.Int
		started_at: DF.Datetime | None
		status: DF.Literal["Queued", "Running", "Completed", "Failed", "Stale"]
		warnings: DF.Int
	# end: auto-generated types

	def validate(self):
		if not frappe.flags.get(TRANSITION_FLAG):
			frappe.throw(_("Check runs are written only by the close service."), frappe.PermissionError)

	def on_trash(self):
		if not frappe.flags.get(TRANSITION_FLAG):
			frappe.throw(_("Check runs are part of the close evidence and cannot be deleted."))
