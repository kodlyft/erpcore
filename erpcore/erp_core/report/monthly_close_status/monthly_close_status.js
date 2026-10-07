// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.query_reports["Monthly Close Status"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "from_month", label: __("From Month"), fieldtype: "Date" },
		{ fieldname: "to_month", label: __("To Month"), fieldtype: "Date" },
		{
			fieldname: "state",
			label: __("State"),
			fieldtype: "Select",
			options: [
				"",
				"Draft",
				"In Progress",
				"Ready for Review",
				"Approved",
				"Closing",
				"Closed",
				"Reopened",
			],
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "lock" && data && data.lock === __("Failed")) {
			value = `<span class="text-danger">${value}</span>`;
		}
		return value;
	},
};
