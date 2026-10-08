# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Give Accounting Periods owned by a Monthly Close a company-scoped `period_name`.

`period_name` is unique across the site. Earlier releases named owned periods
"Monthly Close YYYY-MM", so a second company could not close the same month.
Only periods this app owns are renamed; externally created periods are never
touched. The document name (which already carries the company abbreviation)
does not change, so links from closes stay valid.
"""

import frappe

from erpcore.erp_core.monthly_close.constants import OWNER_FIELD


def execute():
	if not frappe.db.has_column("Accounting Period", OWNER_FIELD):
		return

	from erpcore.erp_core.monthly_close.native_lock import period_name_for

	for row in frappe.get_all(
		"Accounting Period",
		filters={OWNER_FIELD: ["is", "set"]},
		fields=["name", "period_name", OWNER_FIELD],
	):
		close = frappe.db.get_value(
			"Monthly Close", row.get(OWNER_FIELD), ["company", "period_start"], as_dict=True
		)
		if not close:
			continue
		target = period_name_for(close)
		if row.period_name == target:
			continue
		if frappe.db.exists("Accounting Period", {"period_name": target, "name": ["!=", row.name]}):
			frappe.log_error(
				title="erpcore: owned Accounting Period not renamed",
				message=f"{row.name}: another period already uses the name {target!r}.",
			)
			continue
		# A direct update: the protection hook guards edits made through the document API.
		frappe.db.set_value("Accounting Period", row.name, "period_name", target, update_modified=False)
