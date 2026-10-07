# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Findings of the latest check run of each permitted close, with their exception status."""

import json

import frappe
from frappe import _


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
		{
			"fieldname": "check_run",
			"label": _("Run"),
			"fieldtype": "Link",
			"options": "Monthly Close Check Run",
			"width": 110,
		},
		{"fieldname": "check_label", "label": _("Check"), "fieldtype": "Data", "width": 220},
		{"fieldname": "status", "label": _("Result"), "fieldtype": "Data", "width": 90},
		{"fieldname": "count", "label": _("Count"), "fieldtype": "Int", "width": 70},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Float", "width": 110},
		{"fieldname": "message", "label": _("Details"), "fieldtype": "Data", "width": 360},
		{
			"fieldname": "exception",
			"label": _("Exception"),
			"fieldtype": "Link",
			"options": "Monthly Close Exception",
			"width": 120,
		},
		{"fieldname": "exception_status", "label": _("Exception Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "samples", "label": _("Sample"), "fieldtype": "Data", "width": 300},
	]

	conditions = {"latest_check_run": ["is", "set"]}
	if filters.company:
		conditions["company"] = filters.company
	if filters.monthly_close:
		conditions["name"] = filters.monthly_close

	statuses = ["Warning", "Blocker", "Error"] if not filters.include_passed else None
	data = []
	for close in frappe.get_list(
		"Monthly Close", filters=conditions, fields=["name", "company", "latest_check_run", "revision"]
	):
		result_filters = {"parent": close.latest_check_run, "parenttype": "Monthly Close Check Run"}
		if statuses:
			result_filters["status"] = ["in", statuses]
		waivers = {
			w.finding_signature: w
			for w in frappe.get_all(
				"Monthly Close Exception",
				filters={"monthly_close": close.name, "revision": close.revision},
				fields=["name", "status", "finding_signature"],
				order_by="creation asc",
			)
		}
		for row in frappe.get_all(
			"Monthly Close Check Result",
			filters=result_filters,
			fields=["check_label", "status", "count", "amount", "message", "samples", "finding_signature"],
			order_by="idx asc",
		):
			waiver = waivers.get(row.finding_signature)
			sample = json.loads(row.samples)[:1] if row.samples else []
			data.append(
				{
					"monthly_close": close.name,
					"company": close.company,
					"check_run": close.latest_check_run,
					"check_label": row.check_label,
					"status": _(row.status),
					"count": row.count,
					"amount": row.amount,
					"message": row.message,
					"exception": waiver.name if waiver else None,
					"exception_status": _(waiver.status) if waiver else "",
					"samples": json.dumps(sample, default=str)[:300] if sample else "",
				}
			)

	return columns, data
