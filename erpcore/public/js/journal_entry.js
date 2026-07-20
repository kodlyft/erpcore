// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.ui.form.on("Journal Entry", {
	setup(frm) {
		frm.set_query("cheque_leaf", () => ({
			query: "erpcore.erp_core.doctype.cheque_leaf.cheque_leaf.get_available_leaves",
			filters: {
				company: frm.doc.company,
				voucher_name: frm.doc.name,
			},
		}));
	},

	cheque_leaf(frm) {
		erpcore.cheque.apply_leaf(frm, "cheque_no", "cheque_date");
	},
});
