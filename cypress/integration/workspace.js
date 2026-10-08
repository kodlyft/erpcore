// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

describe("erpcore workspaces and list views", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.erpcore_seed();
	});

	["erp-core", "cheque-management"].forEach((route) => {
		it(`renders the ${route} workspace`, () => {
			cy.visit(`/desk/${route}`);
			cy.get(".widget.shortcut-widget-box").should("have.length.at.least", 1);
			cy.contains(".widget.shortcut-widget-box", "Cheque Book").should("be.visible");
			cy.contains(".widget.shortcut-widget-box", "Cheque Leaf").should("be.visible");
		});
	});

	["Cheque Book", "Cheque Leaf", "Cheque Void Reason", "Gate", "Gate Pass", "Gate Pass Return"].forEach(
		(doctype) => {
			it(`loads the ${doctype} list`, () => {
				cy.go_to_list(doctype);
				cy.get("body").should("have.attr", "data-ajax-state", "complete");
				cy.get(".list-row-container, .no-result").should("exist");
			});
		},
	);
});
