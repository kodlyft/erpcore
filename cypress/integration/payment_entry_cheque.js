// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

describe("Picking a cheque leaf on a Payment Entry", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.erpcore_seed();
	});

	beforeEach(() => {
		cy.visit("/desk");
	});

	function open_payment_with_leaf(amount, leaf) {
		cy.make_payment_entry({ amount }).then((payment) => {
			cy.open_doc("Payment Entry", payment.name);
			cy.window().its("cur_frm").should("exist").invoke("scroll_to_field", "cheque_leaf");
			cy.select_link("cheque_leaf", leaf.name);
			cy.wrap(payment.name).as("payment_name");
		});
	}

	it("offers only unused leaves, fills the reference and reserves the leaf", () => {
		cy.make_cheque_book({ number_of_leaves: 1 }).then((book) => {
			const leaf = book.leaves[0];

			open_payment_with_leaf(500, leaf);

			cy.get_field("reference_no", "Data").should("have.value", leaf.cheque_no);

			cy.save();

			cy.field_value("Cheque Leaf", leaf.name, "status").should("eq", "Reserved");
			cy.get("@payment_name").then((name) => {
				cy.field_value("Cheque Leaf", leaf.name, "reference_name").should("eq", name);
			});
		});
	});

	it("issues the leaf on submit", () => {
		cy.make_cheque_book({ number_of_leaves: 1 }).then((book) => {
			const leaf = book.leaves[0];

			open_payment_with_leaf(250, leaf);

			cy.save();

			cy.intercept("/api/method/frappe.desk.form.save.savedocs").as("submit_call");
			cy.get(".page-container:visible .primary-action").should("contain.text", "Submit");
			cy.click_doc_primary_button("Submit");
			cy.click_modal_primary_button("Yes");
			cy.wait("@submit_call");

			cy.field_value("Cheque Leaf", leaf.name, "status").should("eq", "Issued");
			cy.field_value("Cheque Leaf", leaf.name, "amount").should("eq", 250);
		});
	});
});
