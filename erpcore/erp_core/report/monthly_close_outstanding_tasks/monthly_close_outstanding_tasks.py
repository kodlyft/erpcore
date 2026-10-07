# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Open checklist tasks of the current revision of each close, oldest due date first."""

import frappe
from frappe import _
from frappe.utils import date_diff, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = [
		{
			"fieldname": "task",
			"label": _("Task"),
			"fieldtype": "Link",
			"options": "Monthly Close Task",
			"width": 110,
		},
		{"fieldname": "title", "label": _("Title"), "fieldtype": "Data", "width": 260},
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
			"width": 160,
		},
		{
			"fieldname": "assigned_to",
			"label": _("Assigned To"),
			"fieldtype": "Link",
			"options": "User",
			"width": 150,
		},
		{"fieldname": "due_date", "label": _("Due"), "fieldtype": "Date", "width": 100},
		{"fieldname": "days_overdue", "label": _("Days Overdue"), "fieldtype": "Int", "width": 100},
		{"fieldname": "is_mandatory", "label": _("Mandatory"), "fieldtype": "Check", "width": 90},
	]

	conditions = {"status": "Open"}
	for key in ("company", "monthly_close", "assigned_to"):
		if filters.get(key):
			conditions[key] = filters.get(key)

	tasks = frappe.get_list(
		"Monthly Close Task",
		filters=conditions,
		fields=[
			"name",
			"title",
			"monthly_close",
			"company",
			"assigned_to",
			"due_date",
			"is_mandatory",
			"revision",
		],
		order_by="due_date asc",
	)

	current = {}
	today = getdate(nowdate())
	data = []
	for task in tasks:
		if task.monthly_close not in current:
			current[task.monthly_close] = frappe.db.get_value("Monthly Close", task.monthly_close, "revision")
		if task.revision != current[task.monthly_close]:
			continue
		overdue = date_diff(today, task.due_date) if task.due_date else 0
		data.append({**task, "task": task.name, "days_overdue": max(overdue, 0)})

	return columns, data
