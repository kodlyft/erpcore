// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.query_reports["Monthly Close Audit Trail"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "monthly_close", label: __("Close"), fieldtype: "Link", options: "Monthly Close" },
		{ fieldname: "event_type", label: __("Event Contains"), fieldtype: "Data" },
		{ fieldname: "actor", label: __("By"), fieldtype: "Link", options: "User" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
	],
};
