// Copyright (c) 2026, Kodlyft and contributors
// For license information, please see license.txt

// Buttons are shown by state and role for convenience only. Every action is
// re-authorised and re-validated on the server under a row lock.

const MC_API = "erpcore.erp_core.monthly_close.api";

const mc_has_role = (...roles) =>
	frappe.user.has_role("System Manager") || roles.some((role) => frappe.user.has_role(role));

const mc_call = (frm, method, args = {}, message = null) =>
	frappe
		.call({ method: `${MC_API}.${method}`, args: { name: frm.doc.name, ...args }, freeze: true })
		.then((r) => {
			if (message) frappe.show_alert({ message, indicator: "green" });
			frm.reload_doc();
			return r && r.message;
		});

const mc_reason_dialog = (title, label, callback, description = "") => {
	const dialog = new frappe.ui.Dialog({
		title,
		fields: [{ fieldname: "reason", fieldtype: "Small Text", label, reqd: 1, description }],
		primary_action_label: __("Confirm"),
		primary_action(values) {
			dialog.hide();
			callback(values.reason);
		},
	});
	dialog.show();
};

frappe.ui.form.on("Monthly Close", {
	setup(frm) {
		frm.set_query("preparer", () => ({ filters: { enabled: 1, user_type: "System User" } }));
		frm.set_query("reviewer", () => ({ filters: { enabled: 1, user_type: "System User" } }));
	},

	onload(frm) {
		frappe.realtime.off("monthly_close_updated");
		frappe.realtime.on("monthly_close_updated", (data) => {
			if (data && data.name === frm.doc.name && !frm.is_dirty()) frm.reload_doc();
		});
	},

	refresh(frm) {
		if (frm.is_new()) {
			frm.set_intro(
				__(
					"Pick the company and any date in the month. The close starts as a Draft; nothing is locked until a Close Manager hard-closes it after month end.",
				),
			);
			return;
		}

		frm.disable_save();
		if (["Draft", "In Progress", "Reopened"].includes(frm.doc.state)) frm.enable_save();

		frappe.call({ method: `${MC_API}.get_dashboard`, args: { name: frm.doc.name } }).then((r) => {
			if (!r.message) return;
			frm.__mc = r.message;
			render_dashboard(frm, r.message);
			add_actions(frm, r.message);
		});
	},
});

function add_actions(frm, data) {
	const s = frm.doc.state;
	const group = __("Actions");

	if (s === "Draft" && mc_has_role("Close Preparer", "Close Manager")) {
		frm.add_custom_button(__("Start Close"), () =>
			mc_call(frm, "start", {}, __("Close started")),
		).addClass("btn-primary");
	}

	if (s === "Reopened" && mc_has_role("Close Preparer", "Close Manager")) {
		frm.add_custom_button(__("Resume Work"), () =>
			mc_call(frm, "resume", {}, __("New revision started")),
		);
	}

	if (
		["In Progress", "Ready for Review"].includes(s) &&
		mc_has_role("Close Preparer", "Close Reviewer", "Close Manager")
	) {
		frm.add_custom_button(
			__("Run Checks"),
			() => mc_call(frm, "run_checks", {}, __("Checks queued")),
			group,
		);
	}

	if (s === "In Progress" && mc_has_role("Close Preparer", "Close Manager")) {
		frm.add_custom_button(
			__("Prepare Bank Workpapers"),
			() => mc_call(frm, "prepare_bank_certifications", {}, __("Bank workpapers ready")),
			group,
		);
		frm.add_custom_button(__("Submit for Review"), () =>
			mc_call(frm, "submit_for_review", {}, __("Submitted for review")),
		).addClass("btn-primary");
	}

	if (s === "Ready for Review" && mc_has_role("Close Reviewer", "Close Manager")) {
		frm.add_custom_button(__("Approve"), () => {
			frappe.confirm(
				__("Approve revision {0} against check run {1}? The approval covers exactly these results.", [
					frm.doc.revision,
					frm.doc.submitted_check_run,
				]),
				() => mc_call(frm, "approve", {}, __("Approved")),
			);
		}).addClass("btn-primary");
	}

	if (["Ready for Review", "Approved"].includes(s) && mc_has_role("Close Reviewer", "Close Manager")) {
		frm.add_custom_button(
			__("Send Back"),
			() =>
				mc_reason_dialog(__("Send Back"), __("Reason"), (reason) =>
					mc_call(frm, "send_back", { reason }, __("Sent back")),
				),
			group,
		);
	}

	if (s === "Approved" && mc_has_role("Close Manager")) {
		frm.add_custom_button(__("Hard Close"), () => confirm_hard_close(frm, data)).addClass("btn-danger");
	}

	const pending = data.pending || {};
	const seal_stalled = pending.action === "Seal Packet" && pending.stalled;
	if (s === "Closing" && (frm.doc.last_error || seal_stalled) && mc_has_role("Close Manager")) {
		frm.add_custom_button(
			__("Retry Sealing"),
			() => mc_call(frm, "retry_seal", {}, __("Sealing queued")),
			group,
		);
		frm.add_custom_button(
			__("Abort Close"),
			() =>
				mc_reason_dialog(
					__("Abort Close"),
					__("Reason"),
					(reason) => mc_call(frm, "abort_close", { reason }, __("Close aborted")),
					__("Disables only the Accounting Period this close created and returns it to Approved."),
				),
			group,
		);
	}

	if (s === "Closed") {
		if (mc_has_role("Close Preparer", "Close Manager")) {
			frm.add_custom_button(
				__("Request Reopen"),
				() =>
					mc_reason_dialog(
						__("Request Reopen"),
						__("Why must this month be reopened?"),
						(reason) => mc_call(frm, "request_reopen", { reason }, __("Reopen requested")),
						__("Nothing is unlocked until another Close Manager approves the request."),
					),
				group,
			);
		}
		if (frm.doc.revalidation_required && mc_has_role("Close Reviewer", "Close Manager")) {
			frm.add_custom_button(
				__("Confirm Revalidation"),
				() =>
					mc_reason_dialog(
						__("Confirm Revalidation"),
						__("What did you review?"),
						(comment) => mc_call(frm, "confirm_revalidation", { comment }, __("Revalidated")),
						__(
							"Earlier months must be reclosed first. The checks are rerun against this locked month; changed balances need a reopen and reclose instead.",
						),
					),
				group,
			);
		}
	}

	(data.reopen_requests || [])
		.filter((r) => r.status === "Requested")
		.forEach((r) => {
			if (!mc_has_role("Close Manager")) return;
			frm.add_custom_button(
				__("Approve Reopen {0}", [r.name]),
				() =>
					frappe.confirm(
						__(
							"Reopen {0}? The lock this close owns will be disabled and a new revision started. Reason given: {1}",
							[frm.doc.name, frappe.utils.escape_html(r.reason)],
						),
						() =>
							frappe
								.call({
									method: `${MC_API}.decide_reopen`,
									args: { request: r.name, approve: 1 },
									freeze: true,
								})
								.then(() => frm.reload_doc()),
					),
				__("Reopen Requests"),
			);
			frm.add_custom_button(
				__("Reject Reopen {0}", [r.name]),
				() =>
					mc_reason_dialog(__("Reject Reopen"), __("Reason"), (note) =>
						frappe
							.call({
								method: `${MC_API}.decide_reopen`,
								args: { request: r.name, approve: 0, note },
								freeze: true,
							})
							.then(() => frm.reload_doc()),
					),
				__("Reopen Requests"),
			);
		});

	if (["Closed", "Closing"].includes(s)) {
		frm.add_custom_button(__("Live Packet View"), () => frm.print_doc(), group);
	}
	if (s === "Closed" || cint(frm.doc.revision) > 1) {
		frm.add_custom_button(__("Sealed Packet"), () => mc_open_sealed_packet(frm), group);
	}
}

function mc_open_sealed_packet(frm) {
	const revisions = [];
	for (let r = cint(frm.doc.revision); r >= 1; r--) revisions.push(String(r));
	frappe.prompt(
		[
			{
				fieldname: "revision",
				fieldtype: "Select",
				label: __("Revision"),
				options: revisions,
				default: revisions[0],
				reqd: 1,
			},
		],
		({ revision }) =>
			frappe
				.call({ method: `${MC_API}.export_revision_packet`, args: { name: frm.doc.name, revision } })
				.then(({ message }) => {
					const packet = message["Close Packet"];
					if (!packet) return;
					if (!packet.verified) {
						frappe.msgprint(__("The stored packet no longer matches its recorded hash."));
					}
					window.open(packet.file);
				}),
		__("Open Sealed Packet"),
		__("Open"),
	);
}

function confirm_hard_close(frm, data) {
	const blockers = data.preflight || [];
	const list = blockers.length
		? `<p class="text-danger">${__("These will stop the hard close:")}</p><ul>${blockers
				.map((b) => `<li>${frappe.utils.escape_html(b)}</li>`)
				.join("")}</ul>`
		: `<p>${__("No blockers found right now. The job checks everything again under the lock.")}</p>`;

	frappe.confirm(
		`${list}<p>${__(
			"Hard close will rerun every check, compare the ledgers with the approved fingerprint and then lock {0} for {1} with an Accounting Period. Postings, cancellations and amendments dated in the month will be refused until it is reopened.",
			[frm.doc.month, frappe.utils.escape_html(frm.doc.company)],
		)}</p>`,
		() => mc_call(frm, "hard_close", {}, __("Hard close queued")),
	);
}

function badge(status) {
	const colours = {
		Passed: "green",
		Warning: "orange",
		Blocker: "red",
		Error: "red",
		"Not Applicable": "gray",
		Healthy: "green",
		Failed: "red",
		"Not Locked": "gray",
		Done: "green",
		Open: "orange",
		Approved: "green",
		Requested: "blue",
		Rejected: "red",
		Certified: "green",
		Draft: "gray",
	};
	return `<span class="indicator-pill ${colours[status] || "gray"}">${__(status)}</span>`;
}

function render_dashboard(frm, data) {
	const e = frappe.utils.escape_html;
	const tasks = data.tasks || [];
	const done = tasks.filter((t) => t.status !== "Open").length;
	const mandatory_open = tasks.filter((t) => t.is_mandatory && t.status !== "Done").length;

	let html = `<div class="mc-dashboard">`;

	html += `<div class="row">
		<div class="col-sm-3"><h6>${__("Checklist")}</h6><p>${done} / ${tasks.length} ${__("done")}${
		mandatory_open ? `<br><span class="text-danger">${mandatory_open} ${__("mandatory open")}</span>` : ""
	}</p></div>
		<div class="col-sm-3"><h6>${__("Checks")}</h6><p>${
		data.check_run
			? `${badge(data.check_run.status)} ${
					data.freshness === "stale"
						? `<span class="text-danger">${__("stale – run again")}</span>`
						: __("current")
			  }<br>${data.check_run.blockers || 0} ${__("blockers")}, ${data.check_run.warnings || 0} ${__(
					"warnings",
			  )}, ${data.check_run.errors || 0} ${__("errors")}`
			: __("Not run yet")
	}${data.pending_run ? `<br>${badge(data.pending_run.status)} ${e(data.pending_run.name)}` : ""}</p></div>
		<div class="col-sm-3"><h6>${__("Posting Lock")}</h6><p>${badge(data.lock.status)}<br>${
		["Closing", "Closed"].includes(data.state)
			? __("Postings in the month are refused.")
			: __("Not locked: normal postings into the month still succeed.")
	}</p></div>
		<div class="col-sm-3"><h6>${__("Revision")}</h6><p>${data.revision}${
		frm.doc.revalidation_required
			? `<br><span class="text-danger">${__("Revalidation required")}</span>`
			: ""
	}</p></div>
	</div>`;

	if (data.state === "Ready for Review" || data.state === "Approved") {
		html += `<p class="text-muted">${__(
			"Soft close: the checklist and evidence are frozen for review, but this is not a posting lock.",
		)}</p>`;
	}

	if (frm.doc.last_error) {
		html += `<div class="alert alert-danger">${e(frm.doc.last_error)}</div>`;
	}

	(data.lock.problems || []).forEach((p) => (html += `<div class="alert alert-danger">${e(p)}</div>`));

	if (
		data.lock_assessment &&
		data.lock_assessment.kind &&
		!["None", "Owned"].includes(data.lock_assessment.kind)
	) {
		html += `<div class="alert alert-warning">${__("Existing Accounting Period")}: ${e(
			(data.lock_assessment.external || []).join(", "),
		)} (${e(data.lock_assessment.kind)}). ${e((data.lock_assessment.problems || []).join(" "))}</div>`;
	}

	if (data.check_run && data.check_run.results.length) {
		const waived = new Set(
			(data.waivers || []).filter((w) => w.status === "Approved").map((w) => w.finding_signature),
		);
		html += `<h6 class="mt-3">${__("Check Results")} – ${e(data.check_run.name)}</h6>
		<table class="table table-bordered table-sm"><thead><tr>
			<th>${__("Check")}</th><th>${__("Result")}</th><th>${__("Details")}</th><th></th></tr></thead><tbody>`;
		data.check_run.results.forEach((r) => {
			const can_waive =
				["Warning", "Blocker"].includes(r.status) &&
				frm.doc.state === "In Progress" &&
				mc_has_role("Close Preparer", "Close Manager") &&
				!waived.has(r.finding_signature);
			html += `<tr><td>${e(r.check_label)}<br><small class="text-muted">${__(r.severity)}</small></td>
				<td>${badge(r.status)}${
				waived.has(r.finding_signature) ? `<br><small>${__("Exception approved")}</small>` : ""
			}</td>
				<td><small>${e(r.message || "")}</small>${
				r.route ? ` <a href="/app/${e(r.route)}">${__("Open")}</a>` : ""
			}</td>
				<td>${
					can_waive
						? `<button class="btn btn-xs btn-default mc-waive" data-row="${e(r.name)}">${__(
								"Request Exception",
						  )}</button>`
						: ""
				}</td></tr>`;
		});
		html += `</tbody></table>`;
	}

	const pending = (data.waivers || []).filter((w) => w.status === "Requested");
	if (pending.length) {
		html += `<h6>${__("Exceptions Awaiting Decision")}</h6><ul>`;
		pending.forEach((w) => {
			html += `<li><a href="/app/monthly-close-exception/${e(w.name)}">${e(w.name)}</a> – ${e(
				w.check_label,
			)} (${e(w.requested_by)})${
				mc_has_role("Close Reviewer", "Close Manager")
					? ` <button class="btn btn-xs btn-default mc-waiver-decide" data-name="${e(
							w.name,
					  )}" data-approve="1">${__(
							"Approve",
					  )}</button> <button class="btn btn-xs btn-default mc-waiver-decide" data-name="${e(
							w.name,
					  )}" data-approve="0">${__("Reject")}</button>`
					: ""
			}</li>`;
		});
		html += `</ul>`;
	}

	if ((data.bank_certifications || []).length) {
		html += `<h6>${__("Bank Workpapers")}</h6><ul>`;
		data.bank_certifications.forEach((b) => {
			html += `<li><a href="/app/monthly-close-bank-certification/${e(b.name)}">${e(
				b.bank_account,
			)}</a> ${badge(b.status)}</li>`;
		});
		html += `</ul>`;
	}

	if (tasks.length) {
		html += `<h6 class="mt-3">${__("Checklist (revision {0})", [data.revision])}</h6>
		<table class="table table-bordered table-sm"><tbody>`;
		tasks.forEach((t) => {
			html += `<tr><td><a href="/app/monthly-close-task/${e(t.name)}">${e(t.title)}</a>${
				t.is_mandatory ? ` <small class="text-muted">${__("mandatory")}</small>` : ""
			}${t.manual_certification ? ` <small class="text-muted">${__("manual review")}</small>` : ""}</td>
			<td>${badge(t.status)}</td><td><small>${e(t.assigned_to || "")} ${
				t.due_date ? frappe.datetime.str_to_user(t.due_date) : ""
			}</small></td></tr>`;
		});
		html += `</tbody></table>`;
	}

	if ((data.events || []).length) {
		html += `<details><summary>${__("History")}</summary><table class="table table-sm"><tbody>`;
		data.events.forEach((ev) => {
			html += `<tr><td><small>${frappe.datetime.str_to_user(ev.event_time)}</small></td><td>r${
				ev.revision
			}</td>
			<td>${e(ev.event_type)}</td><td><small>${e(ev.actor)}</small></td><td><small>${e(ev.from_state || "")} → ${e(
				ev.to_state || "",
			)}</small></td></tr>`;
		});
		html += `</tbody></table></details>`;
	}

	html += `</div>`;
	const wrapper = frm.get_field("dashboard_html").$wrapper;
	wrapper.html(html);

	wrapper.find(".mc-waive").on("click", (ev) => {
		const row = $(ev.currentTarget).attr("data-row");
		const dialog = new frappe.ui.Dialog({
			title: __("Request Exception"),
			fields: [
				{
					fieldname: "explanation",
					fieldtype: "Small Text",
					label: __("Why is this acceptable?"),
					reqd: 1,
				},
				{ fieldname: "evidence", fieldtype: "Attach", label: __("Evidence") },
				{ fieldname: "expires_on", fieldtype: "Date", label: __("Expires On") },
			],
			primary_action_label: __("Request"),
			primary_action(values) {
				dialog.hide();
				mc_call(frm, "request_waiver", { result_row: row, ...values }, __("Exception requested"));
			},
		});
		dialog.show();
	});

	wrapper.find(".mc-waiver-decide").on("click", (ev) => {
		const name = $(ev.currentTarget).attr("data-name");
		const approve = $(ev.currentTarget).attr("data-approve") === "1";
		const send = (note) =>
			frappe
				.call({
					method: `${MC_API}.decide_waiver`,
					args: { waiver: name, approve: approve ? 1 : 0, note },
					freeze: true,
				})
				.then(() => frm.reload_doc());
		if (approve) frappe.confirm(__("Approve exception {0}?", [name]), () => send(null));
		else mc_reason_dialog(__("Reject Exception"), __("Reason"), send);
	});
}
