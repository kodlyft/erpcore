# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpcore.erp_core.monthly_close.constants import TRANSITION_FLAG


class MonthlyCloseEvent(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		actor: DF.Link | None
		company: DF.Link | None
		details: DF.Code | None
		event_time: DF.Datetime | None
		event_type: DF.Data | None
		from_state: DF.Data | None
		monthly_close: DF.Link | None
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		revision: DF.Int
		to_state: DF.Data | None
	# end: auto-generated types

	def validate(self):
		if not self.is_new():
			frappe.throw(_("Close events are append-only."), frappe.PermissionError)

	def on_trash(self):
		# Only a never-started Draft close removes its own creation event.
		if not frappe.flags.get(TRANSITION_FLAG):
			frappe.throw(_("Close events are append-only and cannot be deleted."), frappe.PermissionError)
