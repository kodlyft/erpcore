// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

describe("Cheque Book", () => {
	let fixtures;

	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.erpcore_seed().then((seeded) => (fixtures = seeded));
	});

	beforeEach(() => {
		cy.visit("/desk");
	});

	function next_free_number() {
		return cy
			.call("frappe.client.get_list", {
				doctype: "Cheque Leaf",
				filters: { bank_account: fixtures.bank_account },
				fields: ["cheque_number"],
				order_by: "cheque_number desc",
				limit_page_length: 1,
			})
			.then((r) => (r.message.length ? r.message[0].cheque_number : 100) + 1);
	}

	function fill_book(start) {
		cy.new_form("Cheque Book");
		cy.fill_field("company", fixtures.company, "Link");
		cy.fill_field("bank_account", fixtures.bank_account, "Link");
		cy.fill_field("prefix", "CHQ", "Data");
		cy.fill_field("starting_number", String(start), "Int");
		cy.fill_field("number_of_leaves", "3", "Int");
		cy.save();
	}

	it("generates one leaf per number on submit", () => {
		next_free_number().then((start) => {
			fill_book(start);

			cy.get('[data-fieldname="first_cheque_no"]').should(
				"contain.text",
				`CHQ${String(start).padStart(6, "0")}`,
			);

			cy.click_doc_primary_button("Submit");
			cy.click_modal_primary_button("Yes");
			cy.get("body").should("have.attr", "data-ajax-state", "complete");

			cy.get("body")
				.invoke("attr", "data-route")
				.then((route) => {
					const name = route.split("/").pop();

					cy.field_value("Cheque Book", name, "status").should("eq", "Active");
					cy.field_value("Cheque Book", name, "leaves_generated").should("eq", 3);
					cy.field_value("Cheque Book", name, "generation_status").should("eq", "Completed");

					cy.call("frappe.client.get_list", {
						doctype: "Cheque Leaf",
						filters: { cheque_book: name },
						fields: ["cheque_no", "status"],
						order_by: "cheque_number",
					}).then((leaves) => {
						expect(leaves.message).to.have.length(3);
						expect(leaves.message[0].cheque_no).to.eq(`CHQ${String(start).padStart(6, "0")}`);
						leaves.message.forEach((leaf) => expect(leaf.status).to.eq("Unused"));
					});
				});
		});
	});

	it("refuses a book that overlaps numbers already on the account", () => {
		cy.make_cheque_book({ number_of_leaves: 3 }).then((book) => {
			// start one number into the book that was just created
			fill_book(book.starting_number + 1);

			cy.click_doc_primary_button("Submit");
			cy.click_modal_primary_button("Yes");

			cy.get_open_dialog().should("contain.text", "Overlapping Cheque Book");
		});
	});
});
