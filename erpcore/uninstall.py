# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from erpcore.erp_core.monthly_close.constants import OWNER_FIELD


def before_uninstall():
	"""Refuse to strand or silently unlock months that a close is holding."""
	if not frappe.db.has_column("Accounting Period", OWNER_FIELD):
		return

	active = frappe.get_all(
		"Accounting Period",
		filters={OWNER_FIELD: ["is", "set"], "disabled": 0},
		fields=["name", OWNER_FIELD],
	)
	if not active:
		return

	frappe.throw(
		_(
			"ERP Core cannot be uninstalled while Monthly Closes hold active locks: {0}. "
			"Decide for each month whether it stays closed. To keep it closed after uninstalling, a System "
			"Manager uses 'Detach Lock' on the close, which turns the period into an ordinary native "
			"Accounting Period that stays enabled. To unlock it, reopen the close instead. Then uninstall again."
		).format(", ".join(f"{row.name} ({row.get(OWNER_FIELD)})" for row in active)),
		title=_("Active Close Locks"),
	)
