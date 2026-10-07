# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Close, review, lock and reopen history. Read through `frappe.get_list`, so company permissions apply."""

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = [
		{"fieldname": "event_time", "label": _("Time"), "fieldtype": "Datetime", "width": 160},
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
		{"fieldname": "revision", "label": _("Rev"), "fieldtype": "Int", "width": 50},
		{"fieldname": "event_type", "label": _("Event"), "fieldtype": "Data", "width": 180},
		{"fieldname": "actor", "label": _("By"), "fieldtype": "Link", "options": "User", "width": 160},
		{"fieldname": "from_state", "label": _("From"), "fieldtype": "Data", "width": 110},
		{"fieldname": "to_state", "label": _("To"), "fieldtype": "Data", "width": 110},
		{
			"fieldname": "reference_name",
			"label": _("Reference"),
			"fieldtype": "Dynamic Link",
			"options": "reference_doctype",
			"width": 140,
		},
		{"fieldname": "reference_doctype", "label": _("Reference Type"), "fieldtype": "Data", "hidden": 1},
	]

	conditions = {}
	for key in ("company", "monthly_close", "actor"):
		if filters.get(key):
			conditions[key] = filters.get(key)
	if filters.event_type:
		conditions["event_type"] = ["like", f"%{filters.event_type}%"]
	if filters.from_date and filters.to_date:
		conditions["event_time"] = ["between", [filters.from_date, f"{filters.to_date} 23:59:59"]]

	data = frappe.get_list(
		"Monthly Close Event",
		filters=conditions,
		fields=[
			"event_time",
			"monthly_close",
			"company",
			"revision",
			"event_type",
			"actor",
			"from_state",
			"to_state",
			"reference_doctype",
			"reference_name",
		],
		order_by="event_time desc, creation desc",
		limit_page_length=5000,
	)
	return columns, data
