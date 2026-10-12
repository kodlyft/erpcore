// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

// Drives a close through the Desk form: start, review, approve, hard close,
// reopen request and approval, resume, and the reclose of revision 2, then checks
// that revision 1's sealed packet survived unchanged. Hard close and checks run in
// background workers, so this spec needs `bench start` (or a worker) for the site.

const API = "erpcore.erp_core.monthly_close.api";

const wait_for_state = (name, state, attempts = 30) => {
	cy.field_value("Monthly Close", name, "state").then((value) => {
		if (value === state || attempts <= 0) {
			expect(value).to.equal(state);
			return;
		}
		cy.wait(2000);
		wait_for_state(name, state, attempts - 1);
	});
};

const review_approve_and_close = (name) => {
	cy.prepare_monthly_close_for_review(name);
	cy.open_doc("Monthly Close", name);
	cy.contains(".mc-dashboard", "Check Results").should("be.visible");
	cy.findByRole("button", { name: "Submit for Review" }).click();
	wait_for_state(name, "Ready for Review");

	cy.open_doc("Monthly Close", name);
	cy.findByRole("button", { name: "Approve" }).click();
	cy.get(".modal:visible").contains("button", "Yes").click();
	wait_for_state(name, "Approved");

	cy.open_doc("Monthly Close", name);
	cy.findByRole("button", { name: "Hard Close" }).click();
	cy.get(".modal:visible").should(
		"contain",
		"Postings, cancellations and amendments dated in the month will be refused",
	);
	cy.get(".modal:visible").contains("button", "Yes").click();
	wait_for_state(name, "Closed");
};

const sealed_packet = (name, revision) =>
	cy.call(`${API}.export_revision_packet`, { name, revision }).then((r) => r.message["Close Packet"]);

const sealed_revision = (name, revision) =>
	cy.call(`${API}.export_revision_packet`, { name, revision }).then((r) => r.message);

// What must never change once sealed: file and hash of the manifest, packet and every report.
const sealed_identity = (revision) =>
	[revision["Close Manifest"], revision["Close Packet"], ...revision.reports].map((row) => ({
		file: row.file,
		sha256: row.sha256,
		verified: row.verified,
	}));

describe("Monthly Close", () => {
	let close;

	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.reset_monthly_close().then((r) => (close = r));
	});

	it("shows the workspace and list", () => {
		cy.visit("/desk/monthly-closing");
		cy.contains(".widget.shortcut-widget-box", "Monthly Close").should("be.visible");
		cy.go_to_list("Monthly Close");
		cy.get(".list-row-container").should("exist");
	});

	it("starts the close and shows the checklist", () => {
		cy.open_doc("Monthly Close", close.name);
		cy.contains("Not locked: normal postings into the month still succeed.").should("be.visible");
		cy.findByRole("button", { name: "Start Close" }).click();
		cy.contains(".mc-dashboard", "Checklist (revision 1)").should("be.visible");
		cy.contains(".mc-dashboard", "Attach evidence").should("be.visible");
	});

	it("submits, approves and hard-closes", () => {
		review_approve_and_close(close.name);

		cy.open_doc("Monthly Close", close.name);
		cy.contains(".mc-dashboard", "Postings in the month are refused.").should("be.visible");
		cy.contains(".mc-dashboard", "Healthy").should("be.visible");
		sealed_packet(close.name, 1).then((packet) => {
			expect(packet.verified).to.equal(true);
			expect(packet.status).to.equal("Original");
		});
	});

	it("reopens through a request and starts revision 2", () => {
		cy.open_doc("Monthly Close", close.name);
		cy.click_grouped_button("Actions", "Request Reopen");
		cy.get(".modal:visible textarea").type("Late supplier invoice");
		cy.get(".modal:visible").contains("button", "Confirm").click();
		cy.field_value("Monthly Close", close.name, "state").should("equal", "Closed");

		cy.open_doc("Monthly Close", close.name);
		cy.get(".inner-group-button[data-label='Reopen%20Requests'] button").click();
		cy.get(".inner-group-button[data-label='Reopen%20Requests'] .dropdown-item")
			.first()
			.click({ force: true });
		cy.get(".modal:visible").contains("button", "Yes").click();
		wait_for_state(close.name, "Reopened");

		cy.open_doc("Monthly Close", close.name);
		cy.findByRole("button", { name: "Resume Work" }).click();
		cy.contains(".mc-dashboard", "Checklist (revision 2)").should("be.visible");
	});

	it("recloses revision 2 and keeps revision 1's manifest, snapshots and packet as sealed", () => {
		sealed_revision(close.name, 1).then((before) => {
			expect(before.reports.length).to.be.greaterThan(0);
			review_approve_and_close(close.name);
			cy.field_value("Monthly Close", close.name, "revision").should("equal", 2);

			sealed_revision(close.name, 1).then((after) => {
				expect(sealed_identity(after)).to.deep.equal(sealed_identity(before));
				sealed_identity(after).forEach((row) => expect(row.verified).to.equal(true));
				[after["Close Manifest"], after["Close Packet"], ...after.reports].forEach((row) =>
					expect(row.status).to.equal("Superseded"),
				);
			});
			sealed_revision(close.name, 2).then((second) => {
				sealed_identity(second).forEach((row) => expect(row.verified).to.equal(true));
				expect(second["Close Packet"].status).to.equal("Original");
				expect(second["Close Packet"].sha256).not.to.equal(before["Close Packet"].sha256);
				expect(second.reports.length).to.equal(before.reports.length);
			});
		});

		cy.open_doc("Monthly Close", close.name);
		cy.contains(".mc-dashboard", "Healthy").should("be.visible");
	});
});
