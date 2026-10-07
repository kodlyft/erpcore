# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Is every Closing/Closed month still locked as recorded, and are its snapshots unaltered?

A closed parent with a disabled, deleted, narrowed or exempted Accounting Period
is a control failure, not a healthy close.
"""

import frappe
from frappe import _

from erpcore.erp_core.monthly_close import native_lock, snapshot
from erpcore.erp_core.monthly_close.constants import LOCKED_STATES


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = [
		{
			"fieldname": "monthly_close",
			"label": _("Close"),
			"fieldtype": "Link",
			"options": "Monthly Close",
			"width": 160,
		},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 150,
		},
		{"fieldname": "state", "label": _("State"), "fieldtype": "Data", "width": 90},
		{
			"fieldname": "accounting_period",
			"label": _("Accounting Period"),
			"fieldtype": "Link",
			"options": "Accounting Period",
			"width": 220,
		},
		{"fieldname": "ownership", "label": _("Ownership"), "fieldtype": "Data", "width": 100},
		{"fieldname": "lock_status", "label": _("Lock"), "fieldtype": "Data", "width": 80},
		{"fieldname": "snapshots", "label": _("Snapshots"), "fieldtype": "Data", "width": 140},
		{"fieldname": "problems", "label": _("Problems"), "fieldtype": "Data", "width": 420},
	]

	conditions = {"state": ["in", list(LOCKED_STATES)]}
	if filters.company:
		conditions["company"] = filters.company

	data = []
	for name in frappe.get_list(
		"Monthly Close", filters=conditions, pluck="name", order_by="period_start desc"
	):
		close = frappe.get_doc("Monthly Close", name)
		health = native_lock.lock_health(close)
		problems = list(health["problems"])

		snaps = frappe.get_all(
			"Monthly Close Snapshot",
			filters={"monthly_close": name, "revision": close.revision},
			pluck="name",
		)
		snapshot_status = _("{0} stored").format(len(snaps))
		if filters.verify_hashes:
			bad = [s for s in snaps if not snapshot.verify(s)["ok"]]
			snapshot_status = _("{0} of {1} verified").format(len(snaps) - len(bad), len(snaps))
			problems += [_("Snapshot {0} does not match its hash.").format(s) for s in bad]

		data.append(
			{
				"monthly_close": name,
				"company": close.company,
				"state": _(close.state),
				"accounting_period": close.accounting_period,
				"ownership": _("Owned")
				if close.lock_owned
				else (_("External") if close.external_accounting_period else ""),
				"lock_status": _("Failed") if problems else _("Healthy"),
				"snapshots": snapshot_status,
				"problems": " ".join(problems),
			}
		)

	return columns, data
