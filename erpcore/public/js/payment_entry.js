// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.ui.form.on("Payment Entry", {
	setup(frm) {
		frm.set_query("cheque_leaf", () => ({
			query: "erpcore.erp_core.doctype.cheque_leaf.cheque_leaf.get_available_leaves",
			filters: {
				company: frm.doc.company,
				bank_account: frm.doc.bank_account,
				voucher_name: frm.doc.name,
			},
		}));
	},

	refresh(frm) {
		erpcore.cheque.derive_bank_account(frm);
	},

	payment_type(frm) {
		erpcore.cheque.derive_bank_account(frm);
	},

	paid_from(frm) {
		erpcore.cheque.derive_bank_account(frm);
	},

	paid_to(frm) {
		erpcore.cheque.derive_bank_account(frm);
	},

	cheque_leaf(frm) {
		erpcore.cheque.apply_leaf(frm, "reference_no", "reference_date");
	},
});
