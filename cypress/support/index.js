import "../../../frappe/cypress/support/commands";

import "./commands";

Cypress.on("uncaught:exception", () => false);
