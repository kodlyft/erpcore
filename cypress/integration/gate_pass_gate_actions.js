// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

describe("Gate actions on a submitted Gate Pass", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.erpcore_seed();
	});

	beforeEach(() => {
		cy.visit("/desk");
	});

	it("verifies, then releases an outward pass", () => {
		cy.make_gate_pass({ direction: "Outward" }).then((pass) => {
			cy.open_doc("Gate Pass", pass.name);

			cy.click_grouped_button("Gate", "Verify");
			cy.field_value("Gate Pass", pass.name, "guard_verified").should("eq", 1);
			cy.field_value("Gate Pass", pass.name, "status").should("eq", "At Gate");

			// Verify drops out of the menu once it has been done
			cy.get(".custom-actions .inner-group-button[data-label='Gate'] button").click();
			cy.get(
				".custom-actions .inner-group-button[data-label='Gate'] .dropdown-menu [data-label='Verify']",
			).should("not.exist");
			cy.get("body").click(0, 0);

			cy.click_grouped_button("Gate", "Check Out");
			cy.field_value("Gate Pass", pass.name, "status").should("eq", "Exited");
			cy.field_value("Gate Pass", pass.name, "checked_out_at").should("not.be.null");
		});
	});

	it("records arrival on an inward pass", () => {
		cy.make_gate_pass({ direction: "Inward" }).then((pass) => {
			cy.open_doc("Gate Pass", pass.name);

			cy.click_grouped_button("Gate", "Check In");
			cy.field_value("Gate Pass", pass.name, "checked_in_at").should("not.be.null");

			cy.click_grouped_button("Gate", "Verify");
			cy.click_grouped_button("Gate", "Check Out");

			cy.field_value("Gate Pass", pass.name, "status").should("eq", "Received");
		});
	});

	it("refuses to release unverified material", () => {
		cy.make_gate_pass({ direction: "Outward" }).then((pass) => {
			cy.open_doc("Gate Pass", pass.name);

			cy.click_grouped_button("Gate", "Check Out");

			cy.get_open_dialog().should("contain.text", "Verification Required");
			cy.field_value("Gate Pass", pass.name, "checked_out_at").should("be.null");
		});
	});

	it("closes a finished pass", () => {
		cy.make_gate_pass({ direction: "Outward", verified: 1 }).then((pass) => {
			cy.open_doc("Gate Pass", pass.name);

			cy.click_custom_action_button("Close");

			cy.field_value("Gate Pass", pass.name, "status").should("eq", "Closed");
		});
	});
});
