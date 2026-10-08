// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

frappe.ui.form.on("Gate Pass", {
	setup(frm) {
		frm.set_query("party_type", () => ({
			filters: { name: ["in", ["Supplier", "Customer", "Employee"]] },
		}));

		frm.set_query("gate", () => ({
			filters: { company: frm.doc.company, disabled: 0 },
		}));

		frm.set_query("item_code", "items", () => ({
			filters: { disabled: 0 },
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(
				__("Purchase Order"),
				() => pull_items(frm, "Purchase Order"),
				__("Get Items From"),
			);
			frm.add_custom_button(
				__("Delivery Note"),
				() => pull_items(frm, "Delivery Note"),
				__("Get Items From"),
			);
			frm.add_custom_button(
				__("Stock Entry"),
				() => pull_items(frm, "Stock Entry"),
				__("Get Items From"),
			);
		}

		if (frm.doc.docstatus === 1) {
			add_guard_buttons(frm);

			if (frm.doc.returnable && frm.doc.return_status !== "Fully Returned") {
				frm.add_custom_button(
					__("Return"),
					() => {
						frappe.model.open_mapped_doc({
							method: "erpcore.erp_core.doctype.gate_pass_return.gate_pass_return.make_gate_pass_return",
							frm,
						});
					},
					__("Create"),
				);
			}

			if (
				frm.doc.status !== "Closed" &&
				(!frm.doc.returnable || frm.doc.return_status === "Fully Returned")
			) {
				frm.add_custom_button(__("Close"), () =>
					frm.call({ doc: frm.doc, method: "close" }).then(() => frm.reload_doc()),
				);
			}
		}
	},

	direction(frm) {
		set_party_default(frm);
	},

	returnable(frm) {
		frm.set_df_property("expected_return_date", "reqd", frm.doc.returnable ? 1 : 0);
	},
});

frappe.ui.form.on("Gate Pass Item", {
	qty(frm, cdt, cdn) {
		set_amount(frm, cdt, cdn);
	},
	rate(frm, cdt, cdn) {
		set_amount(frm, cdt, cdn);
	},
});

function set_amount(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "amount", flt(row.qty) * flt(row.rate));
}

function set_party_default(frm) {
	if (frm.doc.party) return;
	frm.set_value("party_type", frm.doc.direction === "Inward" ? "Supplier" : "Customer");
}

function add_guard_buttons(frm) {
	const guard_action = (method, label) => {
		frm.add_custom_button(
			__(label),
			() =>
				frm
					.call({ doc: frm.doc, method, freeze: true, freeze_message: __("Recording…") })
					.then(() => {
						frm.reload_doc();
					}),
			__("Gate"),
		);
	};

	if (!frm.doc.guard_verified) guard_action("verify", "Verify");
	if (frm.doc.direction === "Inward" && !frm.doc.checked_in_at) guard_action("check_in", "Check In");
	if (!frm.doc.checked_out_at) guard_action("check_out", "Check Out");
}

function pull_items(frm, source_doctype) {
	const config = {
		"Purchase Order": { child: "Purchase Order Item", status: ["To Receive and Bill", "To Receive"] },
		"Delivery Note": { child: "Delivery Note Item", status: ["To Bill", "Completed"] },
		"Stock Entry": { child: "Stock Entry Detail", status: null },
	}[source_doctype];

	const filters = { docstatus: 1, company: frm.doc.company };
	if (config.status) filters.status = ["in", config.status];

	new frappe.ui.form.MultiSelectDialog({
		doctype: source_doctype,
		target: frm,
		setters: {},
		add_filters_group: 1,
		get_query: () => ({ filters }),
		action(selections) {
			if (!selections || !selections.length) return;

			const existing = new Set(
				(frm.doc.items || []).map((row) => `${row.reference_name}::${row.reference_item_row}`),
			);
			const collected = [];

			frappe.call({
				method: "erpcore.erp_core.doctype.gate_pass.gate_pass.get_source_items",
				args: { source_doctype, source_names: selections, child_doctype: config.child },
				freeze: true,
				callback(r) {
					(r.message || []).forEach((item) => {
						const key = `${item.reference_name}::${item.reference_item_row}`;
						if (existing.has(key)) return;
						existing.add(key);
						collected.push(item);
					});

					collected.forEach((item) => {
						const row = frm.add_child("items");
						Object.assign(row, item);
					});

					frm.refresh_field("items");
					frm.trigger("validate_totals");
					cur_dialog && cur_dialog.hide();

					if (!collected.length) {
						frappe.show_alert({
							message: __("Those items are already on this gate pass."),
							indicator: "orange",
						});
					}
				},
			});
		},
	});
}
