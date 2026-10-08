// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.ui.form.on("Gate Pass Return", {
	setup(frm) {
		frm.set_query("return_against", () => ({
			filters: {
				docstatus: 1,
				returnable: 1,
				return_status: ["!=", "Fully Returned"],
			},
		}));

		frm.set_query("gate", () => ({
			filters: { company: frm.doc.company, disabled: 0 },
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.return_against) {
			frm.add_custom_button(__("Gate Pass"), () => {
				frappe.set_route("Form", "Gate Pass", frm.doc.return_against);
			});
		}
	},
});
