# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpcore.erp_core.monthly_close.constants import TRANSITION_FLAG


class MonthlyCloseReopenRequest(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link | None
		decided_at: DF.Datetime | None
		decided_by: DF.Link | None
		decision_note: DF.SmallText | None
		monthly_close: DF.Link | None
		reason: DF.SmallText | None
		requested_at: DF.Datetime | None
		requested_by: DF.Link | None
		revision: DF.Int
		self_approval_used: DF.Check
		status: DF.Literal["Requested", "Approved", "Rejected"]
	# end: auto-generated types

	def validate(self):
		if not frappe.flags.get(TRANSITION_FLAG):
			frappe.throw(
				_("Reopen requests are raised and decided from the Monthly Close form."),
				frappe.PermissionError,
			)

	def on_trash(self):
		if not frappe.flags.get(TRANSITION_FLAG):
			frappe.throw(_("Reopen requests are part of the close history and cannot be deleted."))
