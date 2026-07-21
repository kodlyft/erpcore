// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

describe("Gate Pass approval workflow", () => {
	let fixtures;

	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.erpcore_seed().then((seeded) => (fixtures = seeded));
	});

	beforeEach(() => {
		cy.visit("/desk");
	});

	it("goes Draft -> Pending Approval -> Approved", () => {
		cy.new_form("Gate Pass");
		cy.select_link("company", fixtures.company);
		cy.select_link("party", fixtures.supplier);
		cy.fill_field("party_name", "_Test Carrier", "Data");
		cy.fill_field("carrying_by", "_Test Driver", "Data");
		cy.get('[data-fieldname="purpose"] textarea').type("Cypress workflow run", { force: true });
		cy.select_link("gate", fixtures.gate);
		cy.window().its("cur_frm").should("exist").invoke("set_value", "posting_time", "10:00:00");
		const row = '.frappe-control[data-fieldname="items"] [data-idx="1"]';
		cy.get(`${row} .grid-static-col[data-fieldname="item_code"]`).click();
		cy.select_link("item_code", "_Test Item", { within: row });
		cy.get(`${row} .grid-static-col[data-fieldname="qty"]`).click();
		cy.get(`${row} [data-fieldname="qty"] input`).clear({ force: true }).type("10", { force: true });
		cy.get(`${row} .grid-static-col[data-fieldname="uom"]`).click();
		cy.select_link("uom", fixtures.uom, { within: row });
		cy.get("body").click(0, 0);

		cy.save();

		// pass_type is derived from direction + returnable
		cy.get('[data-fieldname="pass_type"]').should("contain.text", "NRGP");
		cy.get(".indicator-pill").should("contain.text", "Draft");

		cy.apply_workflow_action("Submit for Approval");
		cy.get(".indicator-pill").should("contain.text", "Pending Approval");

		cy.apply_workflow_action("Approve");
		cy.get(".indicator-pill").should("contain.text", "Approved");

		cy.get("body")
			.invoke("attr", "data-route")
			.then((route) => {
				const name = route.split("/").pop();
				cy.field_value("Gate Pass", name, "docstatus").should("eq", 1);
				cy.field_value("Gate Pass", name, "status").should("eq", "Approved");
			});
	});

	it("can be rejected instead", () => {
		cy.make_gate_pass({ submit: 0 }).then((pass) => {
			cy.open_doc("Gate Pass", pass.name);

			cy.apply_workflow_action("Submit for Approval");
			cy.apply_workflow_action("Reject");

			cy.field_value("Gate Pass", pass.name, "status").should("eq", "Rejected");
			cy.field_value("Gate Pass", pass.name, "docstatus").should("eq", 0);
		});
	});
});
