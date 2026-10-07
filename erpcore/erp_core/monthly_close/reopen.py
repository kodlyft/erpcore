# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Controlled reopening of a closed month.

A request only records intent and a reason; nothing is unlocked until a
different Close Manager approves it. Approval:

* disables only the Accounting Period this close owns. An associated external
  period, any other period and the company freeze date are left alone, so a
  stricter native control still applies;
* marks the revision's snapshots Superseded (they are never overwritten or
  deleted) and starts a new revision;
* sends later months that are under review or approved back to In Progress,
  because their opening balances may change. Later Closed months keep their
  locks and are flagged for revalidation.

Lock order: this close row first, then later closes in ascending month order,
then Accounting Periods. Hard close takes its own row first and only reads
earlier months, so the two cannot deadlock.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpcore.erp_core.monthly_close import native_lock
from erpcore.erp_core.monthly_close.check_runner import service_write
from erpcore.erp_core.monthly_close.constants import (
	APPROVED,
	CLOSE_DOCTYPE,
	CLOSED,
	CLOSING,
	EXCEPTION_DOCTYPE,
	READY_FOR_REVIEW,
	REOPEN_DOCTYPE,
	REOPENED,
	REQUEST_APPROVED,
	REQUEST_REJECTED,
	REQUESTED,
	ROLE_MANAGER,
	ROLE_PREPARER,
	SNAPSHOT_DOCTYPE,
	SNAPSHOT_ORIGINAL,
	SNAPSHOT_SUPERSEDED,
	SUPERSEDED,
)
from erpcore.erp_core.monthly_close.events import log_event
from erpcore.erp_core.monthly_close.lifecycle import (
	get_policy,
	invalidate_approval,
	lock_close,
	require_state,
	set_state,
)
from erpcore.erp_core.monthly_close.notifications import notify_after_commit, transition_message
from erpcore.erp_core.monthly_close.permissions import require_different_actor, require_manager, require_role
from erpcore.erp_core.monthly_close.posting_guard import acquire_close_gate


def request_reopen(name: str, reason: str) -> str:
	require_role(ROLE_PREPARER, ROLE_MANAGER)
	if not (reason or "").strip():
		frappe.throw(_("A reason is required to request reopening a closed month."))

	close = lock_close(name)
	require_state(close, CLOSED)

	existing = frappe.db.exists(REOPEN_DOCTYPE, {"monthly_close": close.name, "status": REQUESTED})
	if existing:
		frappe.throw(_("Reopen request {0} is already waiting for a decision.").format(frappe.bold(existing)))

	request = frappe.get_doc(
		{
			"doctype": REOPEN_DOCTYPE,
			"monthly_close": close.name,
			"company": close.company,
			"revision": close.revision,
			"reason": reason,
			"status": REQUESTED,
			"requested_by": frappe.session.user,
			"requested_at": now_datetime(),
		}
	)
	with service_write():
		request.insert(ignore_permissions=True)

	log_event(
		close,
		"Reopen Requested",
		details={"reason": reason},
		reference_doctype=REOPEN_DOCTYPE,
		reference_name=request.name,
	)
	subject, message = transition_message(close, "Reopen Requested", reason)
	notify_after_commit(close, subject, message, roles=[ROLE_MANAGER])
	return request.name


def decide_reopen(request_name: str, approve_it: bool, note: str | None = None) -> None:
	require_manager()
	request = frappe.get_doc(REOPEN_DOCTYPE, request_name)
	close = lock_close(request.monthly_close)
	request = frappe.get_doc(REOPEN_DOCTYPE, request_name, for_update=True)

	if request.status != REQUESTED:
		frappe.throw(_("Reopen request {0} is already {1}.").format(request_name, _(request.status)))
	if not approve_it and not (note or "").strip():
		frappe.throw(_("A reason is required to reject a reopen request."))

	policy = get_policy(close.company)
	self_approved = approve_it and require_different_actor(
		request.requested_by, cint(policy.allow_self_approval), _("a reopen request")
	)

	request.status = REQUEST_APPROVED if approve_it else REQUEST_REJECTED
	request.decided_by = frappe.session.user
	request.decided_at = now_datetime()
	request.decision_note = note
	request.self_approval_used = cint(self_approved)
	with service_write():
		request.save(ignore_permissions=True)

	if not approve_it:
		log_event(
			close,
			"Reopen Rejected",
			details={"note": note},
			reference_doctype=REOPEN_DOCTYPE,
			reference_name=request.name,
		)
		return

	require_state(close, CLOSED)
	reopen(close, request, self_approved)


def reopen(close, request, self_approved: bool) -> None:
	later = _lock_later_closes(close)

	released = native_lock.release(close)
	superseded = _supersede_revision_artifacts(close)

	previous_revision = close.revision
	set_state(
		close,
		REOPENED,
		"Reopened",
		details={
			"reason": request.reason,
			"request": request.name,
			"previous_revision": previous_revision,
			"released_owned_lock": released,
			"external_lock_kept": bool(close.external_accounting_period),
			"superseded_snapshots": superseded,
			"self_approval_policy_used": self_approved,
		},
		revision=cint(previous_revision) + 1,
		reopen_count=cint(close.reopen_count) + 1,
		reopened_by=frappe.session.user,
		reopened_at=now_datetime(),
		submitted_by=None,
		submitted_at=None,
		submitted_check_run=None,
		approved_by=None,
		approved_at=None,
		approved_revision=0,
		approved_check_run=None,
		approved_fingerprint=None,
		latest_check_run=None,
		latest_fingerprint=None,
		closing_check_run=None,
		closing_fingerprint=None,
		revalidation_required=0,
	)

	for later_close in later:
		_invalidate_later(later_close, close)

	subject, message = transition_message(close, "Reopened", request.reason)
	notify_after_commit(
		close, subject, message, roles=[ROLE_MANAGER, ROLE_PREPARER], users=[request.requested_by]
	)


def _lock_later_closes(close) -> list:
	names = frappe.get_all(
		CLOSE_DOCTYPE,
		filters={"company": close.company, "period_start": [">", close.period_start]},
		order_by="period_start asc",
		pluck="name",
	)
	locked = []
	for name in names:
		acquire_close_gate(name)
		locked.append(frappe.get_doc(CLOSE_DOCTYPE, name))
	return locked


def _invalidate_later(later_close, reopened) -> None:
	reason = _("Earlier month {0} was reopened; its balances carry into this month.").format(reopened.name)
	if later_close.state in (READY_FOR_REVIEW, APPROVED):
		invalidate_approval(later_close, "Invalidated by Earlier Reopen", reason)
	elif later_close.state in (CLOSED, CLOSING):
		with service_write():
			later_close.db_set("revalidation_required", 1)
		log_event(later_close, "Revalidation Required", details={"reason": reason, "reopened": reopened.name})


def _supersede_revision_artifacts(close) -> list[str]:
	snapshots = frappe.get_all(
		SNAPSHOT_DOCTYPE,
		filters={"monthly_close": close.name, "revision": close.revision, "status": SNAPSHOT_ORIGINAL},
		pluck="name",
	)
	for name in snapshots:
		frappe.db.set_value(SNAPSHOT_DOCTYPE, name, "status", SNAPSHOT_SUPERSEDED, update_modified=False)

	for name in frappe.get_all(
		EXCEPTION_DOCTYPE,
		filters={"monthly_close": close.name, "revision": close.revision, "status": REQUESTED},
		pluck="name",
	):
		frappe.db.set_value(EXCEPTION_DOCTYPE, name, "status", SUPERSEDED, update_modified=False)

	return snapshots
