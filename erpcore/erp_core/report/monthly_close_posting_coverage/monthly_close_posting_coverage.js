// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.query_reports["Monthly Close Posting Coverage"] = {
	filters: [],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const colour =
				{ [__("Covered")]: "green", [__("Allowed")]: "blue", [__("Partial")]: "orange" }[
					data.status
				] || "red";
			value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		return value;
	},
};
