// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

const FIXTURES = "erpcore.tests.cypress_fixtures";

Cypress.Commands.add("erpcore_seed", () => {
	return cy.call(`${FIXTURES}.seed`).then((r) => r.message);
});

Cypress.Commands.add("make_cheque_book", (args = {}) => {
	return cy.call(`${FIXTURES}.make_cheque_book`, args).then((r) => r.message);
});

Cypress.Commands.add("make_gate_pass", (args = {}) => {
	return cy.call(`${FIXTURES}.make_gate_pass`, args).then((r) => r.message);
});

Cypress.Commands.add("set_erpcore_setting", (doctype, fieldname, value) => {
	return cy.call(`${FIXTURES}.set_setting`, { doctype, fieldname, value });
});

Cypress.Commands.add("open_doc", (doctype, name) => {
	const route = doctype.toLowerCase().replace(/ /g, "-");
	cy.visit(`/desk/${route}/${encodeURIComponent(name)}`);
	cy.get("body").should("have.attr", "data-ajax-state", "complete");
});

Cypress.Commands.add("apply_workflow_action", (label) => {
	cy.intercept("/api/method/frappe.model.workflow.apply_workflow").as("workflow_call");
	cy.click_action_button(label);
	cy.wait("@workflow_call");
	cy.get("body").should("have.attr", "data-ajax-state", "complete");
});

Cypress.Commands.add("click_grouped_button", (group, label) => {
	cy.get(`.custom-actions .inner-group-button[data-label="${encodeURIComponent(group)}"]`)
		.find("button")
		.click();
	cy.get(
		`.custom-actions .inner-group-button[data-label="${encodeURIComponent(group)}"] ` +
			`.dropdown-menu [data-label="${encodeURIComponent(label)}"]`,
	).click({ force: true });
});

Cypress.Commands.add("field_value", (doctype, name, fieldname) => {
	return cy
		.call("frappe.client.get_value", { doctype, filters: { name }, fieldname })
		.then((r) => r.message[fieldname]);
});

Cypress.Commands.add("make_payment_entry", (args = {}) => {
	return cy.call(`${FIXTURES}.make_payment_entry`, args).then((r) => r.message);
});

Cypress.Commands.add("select_link", (fieldname, value, opts = {}) => {
	const scope = opts.within ? `${opts.within} ` : "";

	const pick = ($options) => {
		const options = $options.toArray();
		return (
			options.find((el) => el.innerText.split("\n")[0].trim() === value) ||
			options.find((el) => el.innerText.includes(value))
		);
	};

	cy.get(`${scope}[data-fieldname="${fieldname}"] input:visible`).first().as("link_input");
	cy.get("@link_input").clear({ force: true }).focus().type(value, { delay: 100 });

	cy.get("[role='option']:visible")
		.should(($options) => {
			expect(pick($options), `dropdown option for "${value}"`).to.exist;
		})
		.then(($options) => cy.wrap(pick($options)).click({ force: true }));

	cy.get("@link_input").should("have.value", value);
});
