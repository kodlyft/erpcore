// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

describe("Returning material against a Gate Pass", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.erpcore_seed();
	});

	beforeEach(() => {
		cy.visit("/desk");
	});

	function open_return_from(pass) {
		cy.open_doc("Gate Pass", pass.name);
		cy.click_grouped_button("Create", "Return");
		cy.get("body").should("have.attr", "data-ajax-state", "complete");
		cy.get_field("return_against", "Link").should("have.value", pass.name);
	}

	function submit_form() {
		cy.save();
		cy.click_doc_primary_button("Submit");
		cy.click_modal_primary_button("Yes");
	}

	it("maps a return that brings everything back", () => {
		cy.make_gate_pass({ returnable: 1, direction: "Outward" }).then((pass) => {
			open_return_from(pass);

			cy.window().its("cur_frm.doc.items").should("have.length", 1);
			cy.window().its("cur_frm.doc.items.0.returned_qty").should("eq", 10);

			submit_form();

			cy.field_value("Gate Pass", pass.name, "return_status").should("eq", "Fully Returned");
			cy.field_value("Gate Pass", pass.name, "per_returned").should("eq", 100);
			cy.field_value("Gate Pass", pass.name, "status").should("eq", "Returned");
		});
	});

	it("tracks a partial return", () => {
		cy.make_gate_pass({ returnable: 1, direction: "Outward" }).then((pass) => {
			open_return_from(pass);

			const cell =
				'.frappe-control[data-fieldname="items"] [data-idx="1"] [data-fieldname="returned_qty"]';
			cy.get(cell).click();
			cy.get(`${cell} input`).clear({ force: true }).type("4", { force: true });
			cy.get("body").click(0, 0);

			submit_form();

			cy.field_value("Gate Pass", pass.name, "return_status").should("eq", "Partly Returned");
			cy.field_value("Gate Pass", pass.name, "per_returned").should("eq", 40);
		});
	});

	it("hides the Return action once nothing is outside", () => {
		cy.make_gate_pass({ returnable: 1, direction: "Outward" }).then((pass) => {
			open_return_from(pass);
			submit_form();

			cy.open_doc("Gate Pass", pass.name);
			cy.get(".custom-actions .inner-group-button[data-label='Create']").should("not.exist");
		});
	});
});
