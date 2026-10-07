// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.query_reports["Monthly Close Outstanding Tasks"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "monthly_close", label: __("Close"), fieldtype: "Link", options: "Monthly Close" },
		{ fieldname: "assigned_to", label: __("Assigned To"), fieldtype: "Link", options: "User" },
	],
};
