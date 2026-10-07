# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Close status by company and month. Rows come from `frappe.get_list`, so company permissions apply."""

import frappe
from frappe import _

from erpcore.erp_core.monthly_close import native_lock


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), data(filters)


def columns():
	return [
		{
			"fieldname": "name",
			"label": _("Close"),
			"fieldtype": "Link",
			"options": "Monthly Close",
			"width": 170,
		},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 170,
		},
		{"fieldname": "month", "label": _("Month"), "fieldtype": "Data", "width": 80},
		{"fieldname": "state", "label": _("State"), "fieldtype": "Data", "width": 120},
		{"fieldname": "revision", "label": _("Rev"), "fieldtype": "Int", "width": 50},
		{"fieldname": "tasks", "label": _("Tasks Done"), "fieldtype": "Data", "width": 90},
		{"fieldname": "checks", "label": _("Latest Checks"), "fieldtype": "Data", "width": 170},
		{"fieldname": "lock", "label": _("Lock"), "fieldtype": "Data", "width": 90},
		{
			"fieldname": "approved_by",
			"label": _("Approved By"),
			"fieldtype": "Link",
			"options": "User",
			"width": 140,
		},
		{"fieldname": "closed_at", "label": _("Closed At"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "revalidation_required", "label": _("Revalidate"), "fieldtype": "Check", "width": 80},
	]


def data(filters):
	conditions = {}
	if filters.company:
		conditions["company"] = filters.company
	if filters.state:
		conditions["state"] = filters.state
	if filters.from_month:
		conditions["period_start"] = [">=", filters.from_month]
	if filters.to_month:
		conditions.setdefault("period_end", ["<=", filters.to_month])

	closes = frappe.get_list(
		"Monthly Close",
		filters=conditions,
		fields=[
			"name",
			"company",
			"month",
			"state",
			"revision",
			"latest_check_run",
			"approved_by",
			"closed_at",
			"revalidation_required",
			"accounting_period",
			"lock_owned",
			"period_start",
			"period_end",
		],
		order_by="period_start desc, company asc",
	)

	rows = []
	for close in closes:
		total = frappe.db.count(
			"Monthly Close Task", {"monthly_close": close.name, "revision": close.revision}
		)
		done = frappe.db.count(
			"Monthly Close Task",
			{"monthly_close": close.name, "revision": close.revision, "status": ["!=", "Open"]},
		)
		checks = ""
		if close.latest_check_run:
			run = frappe.db.get_value(
				"Monthly Close Check Run",
				close.latest_check_run,
				["status", "blockers", "warnings", "errors"],
				as_dict=True,
			)
			if run:
				checks = _("{0}: {1} blockers, {2} warnings, {3} errors").format(
					_(run.status), run.blockers, run.warnings, run.errors
				)

		lock = native_lock.lock_health(frappe.get_doc("Monthly Close", close.name))["status"]
		rows.append({**close, "tasks": f"{done}/{total}", "checks": checks, "lock": _(lock)})

	return rows
