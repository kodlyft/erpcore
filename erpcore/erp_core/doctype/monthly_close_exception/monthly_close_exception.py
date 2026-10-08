# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpcore.erp_core.doctype.monthly_close_task.monthly_close_task import require_private
from erpcore.erp_core.monthly_close.constants import TRANSITION_FLAG


class MonthlyCloseException(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		check_id: DF.Data | None
		check_label: DF.Data | None
		check_result: DF.Data | None
		check_run: DF.Link | None
		check_version: DF.Int
		company: DF.Link | None
		decided_at: DF.Datetime | None
		decided_by: DF.Link | None
		decision_note: DF.SmallText | None
		evidence: DF.Attach | None
		evidence_hash: DF.Data | None
		expires_on: DF.Date | None
		explanation: DF.SmallText | None
		finding_message: DF.SmallText | None
		finding_signature: DF.Data | None
		finding_status: DF.Data | None
		monthly_close: DF.Link | None
		requested_by: DF.Link | None
		revision: DF.Int
		self_approval_used: DF.Check
		status: DF.Literal["Requested", "Approved", "Rejected", "Superseded"]
	# end: auto-generated types

	def validate(self):
		if not frappe.flags.get(TRANSITION_FLAG):
			frappe.throw(
				_("Exceptions are requested and decided from the Monthly Close form."), frappe.PermissionError
			)
		require_private(self.evidence, _("Evidence"))

	def on_trash(self):
		if not frappe.flags.get(TRANSITION_FLAG):
			frappe.throw(_("Exceptions are part of the close evidence and cannot be deleted."))
