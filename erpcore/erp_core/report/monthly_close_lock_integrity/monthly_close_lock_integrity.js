// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.query_reports["Monthly Close Lock Integrity"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "verify_hashes",
			label: __("Verify Snapshot Hashes"),
			fieldtype: "Check",
			description: __("Reads every stored snapshot file. Slower."),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "lock_status" && data && data.problems) {
			value = `<span class="text-danger">${value}</span>`;
		}
		return value;
	},
};
