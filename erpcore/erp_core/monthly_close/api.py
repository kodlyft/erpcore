# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Whitelisted endpoints behind the Monthly Close form buttons.

Every function checks read permission on the close (which applies the company
scope) first, and the service it calls then checks the role and the state from
a locking read of the close row (see lifecycle.lock_close). Nothing here trusts
what the browser shows or hides.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from erpcore.erp_core.monthly_close import closing, lifecycle, native_lock, reopen
from erpcore.erp_core.monthly_close.constants import (
	BANK_CERT_DOCTYPE,
	CHECK_RUN_DOCTYPE,
	CLOSE_DOCTYPE,
	EXCEPTION_DOCTYPE,
	REOPEN_DOCTYPE,
	TASK_DOCTYPE,
)


def load_close(name: str, ptype: str = "read"):
	close = frappe.get_doc(CLOSE_DOCTYPE, name)
	close.check_permission(ptype)
	return close


@frappe.whitelist(methods=["POST"])
def create_close(company: str, period_start: str, template: str | None = None) -> str:
	return lifecycle.create_close(company, period_start, template)


@frappe.whitelist(methods=["POST"])
def start(name: str) -> None:
	load_close(name)
	lifecycle.start_close(name)


@frappe.whitelist(methods=["POST"])
def resume(name: str) -> None:
	load_close(name)
	lifecycle.resume_close(name)


@frappe.whitelist(methods=["POST"])
def run_checks(name: str) -> str:
	load_close(name)
	return lifecycle.request_check_run(name)


@frappe.whitelist(methods=["POST"])
def submit_for_review(name: str) -> None:
	load_close(name)
	lifecycle.submit_for_review(name)


@frappe.whitelist(methods=["POST"])
def approve(name: str, comment: str | None = None) -> None:
	load_close(name)
	lifecycle.approve(name, comment)


@frappe.whitelist(methods=["POST"])
def send_back(name: str, reason: str) -> None:
	load_close(name)
	lifecycle.reject(name, reason)


@frappe.whitelist(methods=["POST"])
def request_waiver(
	name: str, result_row: str, explanation: str, evidence: str | None = None, expires_on: str | None = None
) -> str:
	load_close(name)
	return lifecycle.request_waiver(name, result_row, explanation, evidence, expires_on)


@frappe.whitelist(methods=["POST"])
def decide_waiver(waiver: str, approve: int | str, note: str | None = None) -> None:
	doc = frappe.get_doc(EXCEPTION_DOCTYPE, waiver)
	doc.check_permission("read")
	lifecycle.decide_waiver(waiver, bool(cint(approve)), note)


@frappe.whitelist(methods=["POST"])
def prepare_bank_certifications(name: str) -> list[str]:
	close = load_close(name)
	from erpcore.erp_core.doctype.monthly_close_bank_certification.monthly_close_bank_certification import (
		prepare_for_close,
	)

	return prepare_for_close(close)


@frappe.whitelist(methods=["POST"])
def certify_bank_account(certification: str) -> None:
	doc = frappe.get_doc(BANK_CERT_DOCTYPE, certification)
	doc.check_permission("read")
	lifecycle.certify_bank_account(certification)


@frappe.whitelist(methods=["POST"])
def hard_close(name: str) -> None:
	load_close(name)
	closing.request_hard_close(name)


@frappe.whitelist(methods=["POST"])
def retry_seal(name: str) -> None:
	load_close(name)
	closing.retry_seal(name)


@frappe.whitelist(methods=["POST"])
def abort_close(name: str, reason: str) -> None:
	load_close(name)
	closing.abort_close(name, reason)


@frappe.whitelist(methods=["POST"])
def confirm_revalidation(name: str, comment: str | None = None) -> None:
	load_close(name)
	closing.confirm_revalidation(name, comment)


@frappe.whitelist(methods=["POST"])
def request_reopen(name: str, reason: str) -> str:
	load_close(name)
	return reopen.request_reopen(name, reason)


@frappe.whitelist(methods=["POST"])
def decide_reopen(request: str, approve: int | str, note: str | None = None) -> None:
	doc = frappe.get_doc(REOPEN_DOCTYPE, request)
	doc.check_permission("read")
	reopen.decide_reopen(request, bool(cint(approve)), note)


@frappe.whitelist(methods=["POST"])
def associate_external_period(name: str, accounting_period: str | None = None) -> None:
	load_close(name)
	lifecycle.associate_external_period(name, accounting_period)


@frappe.whitelist(methods=["POST"])
def detach_lock(name: str) -> None:
	load_close(name)
	lifecycle.detach_owned_lock(name)


@frappe.whitelist()
def get_dashboard(name: str) -> dict:
	"""Everything the form needs to render progress, freshness, blockers and history."""
	close = load_close(name)
	from erpcore.erp_core.monthly_close import fingerprint

	tasks = frappe.get_all(
		TASK_DOCTYPE,
		filters={"monthly_close": name, "revision": close.revision},
		fields=[
			"name",
			"title",
			"status",
			"is_mandatory",
			"evidence_required",
			"evidence",
			"assigned_to",
			"due_date",
			"manual_certification",
		],
		order_by="idx asc",
	)

	run = None
	freshness = "none"
	if close.latest_check_run and frappe.db.exists(CHECK_RUN_DOCTYPE, close.latest_check_run):
		run = frappe.get_doc(CHECK_RUN_DOCTYPE, close.latest_check_run)
		freshness = "current"
		if (
			run.revision != close.revision
			or run.policy_version != close.policy_version
			or (run.policy_hash or "") != (close.policy_hash or "")
		):
			freshness = "stale"
		elif close.state not in ("Closing", "Closed"):
			if fingerprint.cached_digest(close) != run.fingerprint:
				freshness = "stale"

	pending_run = frappe.db.get_value(
		CHECK_RUN_DOCTYPE,
		{"monthly_close": name, "revision": close.revision, "status": ["in", ["Queued", "Running"]]},
		["name", "status"],
		as_dict=True,
	)

	waivers = frappe.get_all(
		EXCEPTION_DOCTYPE,
		filters={"monthly_close": name, "revision": close.revision},
		fields=[
			"name",
			"check_label",
			"status",
			"requested_by",
			"decided_by",
			"finding_signature",
			"check_result",
		],
	)
	reopen_requests = frappe.get_all(
		REOPEN_DOCTYPE,
		filters={"monthly_close": name},
		fields=["name", "status", "reason", "requested_by", "decided_by", "revision"],
		order_by="creation desc",
	)
	events = frappe.get_all(
		"Monthly Close Event",
		filters={"monthly_close": name},
		fields=["event_time", "event_type", "actor", "from_state", "to_state", "revision"],
		order_by="event_time desc, creation desc",
		limit=30,
	)

	return {
		"state": close.state,
		"revision": close.revision,
		"tasks": tasks,
		"check_run": (
			{
				"name": run.name,
				"status": run.status,
				"finished_at": run.finished_at,
				"blockers": run.blockers,
				"warnings": run.warnings,
				"errors": run.errors,
				"results": [
					{
						"name": r.name,
						"check_label": r.check_label,
						"status": r.status,
						"severity": r.severity,
						"message": r.message,
						"count": r.count,
						"amount": r.amount,
						"route": r.route,
						"finding_signature": r.finding_signature,
						"waivable": r.waivable,
					}
					for r in run.results
				],
			}
			if run
			else None
		),
		"freshness": freshness,
		"pending_run": pending_run,
		"waivers": waivers,
		"reopen_requests": reopen_requests,
		"lock": native_lock.lock_health(close),
		"pending": {
			"action": close.pending_action,
			"job_id": close.pending_job_id,
			"attempt": close.pending_attempt,
			"requested_at": close.pending_requested_at,
			"started_at": close.pending_started_at,
			"stalled": closing.stage_is_dead(close) if close.pending_action else False,
		},
		"lock_assessment": native_lock.assess(close).__dict__
		if close.state not in ("Closing", "Closed")
		else None,
		"preflight": closing.preflight(close) if close.state == "Approved" else [],
		"events": events,
		"bank_certifications": frappe.get_all(
			BANK_CERT_DOCTYPE,
			filters={"monthly_close": name, "revision": close.revision},
			fields=["name", "bank_account", "status", "unexplained_difference"],
		),
	}


@frappe.whitelist()
def get_packet(name: str) -> dict:
	close = load_close(name)
	if not frappe.has_permission(CLOSE_DOCTYPE, "print", close):
		frappe.throw(_("Not permitted to view the close packet."), frappe.PermissionError)

	from erpcore.erp_core.monthly_close.snapshot import packet_summary

	return json.loads(frappe.as_json(packet_summary(close)))


@frappe.whitelist()
def export_revision_packet(name: str, revision: int | str) -> dict:
	"""The sealed manifest and readable packet of one revision, as stored when it closed."""
	close = load_close(name)
	if not frappe.has_permission(CLOSE_DOCTYPE, "print", close):
		frappe.throw(_("Not permitted to view the close packet."), frappe.PermissionError)

	from erpcore.erp_core.monthly_close.snapshot import revision_packet

	packet = revision_packet(name, cint(revision))
	if not packet:
		frappe.throw(_("Revision {0} of {1} has no sealed packet.").format(revision, name))
	return packet


@frappe.whitelist()
def verify_snapshot(snapshot: str) -> dict:
	doc = frappe.get_doc("Monthly Close Snapshot", snapshot)
	doc.check_permission("read")
	from erpcore.erp_core.monthly_close.snapshot import verify

	return verify(snapshot)
