# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ChequeSettings(Document):
	def validate(self):
		if self.default_padding_length and not 1 <= self.default_padding_length <= 20:
			frappe.throw(_("Default Padding Length must be between 1 and 20."))

		if self.max_leaves_per_book and self.max_leaves_per_book < 1:
			frappe.throw(_("Maximum Leaves Per Book must be at least 1."))
