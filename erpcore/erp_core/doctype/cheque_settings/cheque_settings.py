# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ChequeSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		allow_void_leaf_reuse: DF.Check
		auto_set_cleared_from_clearance_date: DF.Check
		background_generation_threshold: DF.Int
		default_padding_length: DF.Int
		max_leaves_per_book: DF.Int
		pdc_alert_days_before: DF.Int
		require_void_reason: DF.Check
		reserve_leaf_on_draft: DF.Check
		warn_on_postdated_cheque: DF.Check
	# end: auto-generated types

	def validate(self):
		if self.default_padding_length and not 1 <= self.default_padding_length <= 20:
			frappe.throw(_("Default Padding Length must be between 1 and 20."))

		if self.max_leaves_per_book and self.max_leaves_per_book < 1:
			frappe.throw(_("Maximum Leaves Per Book must be at least 1."))
