// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.provide("erpcore.cheque");

Object.assign(erpcore.cheque, {
	derive_bank_account(frm) {
		if (frm.doc.bank_account || frm.doc.docstatus !== 0) return;

		const account = frm.doc.payment_type === "Receive" ? frm.doc.paid_to : frm.doc.paid_from;
		if (!account) return;

		frappe.db
			.get_value("Account", account, "account_type")
			.then((r) => {
				if (!r.message || r.message.account_type !== "Bank") return null;

				return frappe.db.get_list("Bank Account", {
					filters: { account: account, is_company_account: 1, disabled: 0 },
					fields: ["name"],
					limit: 2,
				});
			})
			.then((rows) => {
				if (rows && rows.length === 1) {
					frm.set_value("bank_account", rows[0].name);
				}
			});
	},
	apply_leaf(frm, number_field, date_field) {
		if (!frm.doc.cheque_leaf) return;

		frappe.db.get_value("Cheque Leaf", frm.doc.cheque_leaf, ["cheque_no", "cheque_date"]).then((r) => {
			if (!r.message) return;

			frm.set_value(number_field, r.message.cheque_no);
			if (!frm.doc[date_field]) {
				frm.set_value(date_field, r.message.cheque_date || frm.doc.posting_date);
			}
		});
	},
});
