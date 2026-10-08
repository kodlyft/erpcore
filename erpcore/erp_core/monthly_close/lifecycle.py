# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Close lifecycle: create, start, review, approve, reject, waivers, resume.

Every transition runs the same way:

1. Check role and company access.
2. Take FOR UPDATE on the close row.
3. Re-read the close and check the guard against the locked state.
4. Write the new state with the service flag set.
5. Append an event, then notify after commit.

The `state` field is never writable from forms, REST or imports; the controller
rejects any change made without the service flag.

Hard close, snapshots and reopening live in `closing.py` and `reopen.py`.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime

from erpcore.erp_core.monthly_close import fingerprint
from erpcore.erp_core.monthly_close.check_runner import blocking_results, enqueue_check_run, service_write
from erpcore.erp_core.monthly_close.constants import (
	APPROVED,
	BLOCKER,
	CHECK_RUN_DOCTYPE,
	CLOSE_DOCTYPE,
	DRAFT,
	EDITABLE_STATES,
	ERROR,
	EXCEPTION_DOCTYPE,
	IN_PROGRESS,
	LOCKED_STATES,
	POLICY_DOCTYPE,
	READY_FOR_REVIEW,
	REOPENED,
	REQUEST_APPROVED,
	REQUEST_REJECTED,
	REQUESTED,
	ROLE_MANAGER,
	ROLE_PREPARER,
	ROLE_REVIEWER,
	RUN_COMPLETED,
	SETTINGS_DOCTYPE,
	TASK_DOCTYPE,
	TEMPLATE_DOCTYPE,
	WARNING,
)
from erpcore.erp_core.monthly_close.events import log_event
from erpcore.erp_core.monthly_close.notifications import notify_after_commit, transition_message
from erpcore.erp_core.monthly_close.periods import month_bounds, month_label
from erpcore.erp_core.monthly_close.permissions import (
	require_company_access,
	require_different_actor,
	require_manager,
	require_preparer,
	require_reviewer,
	require_role,
)
from erpcore.erp_core.monthly_close.posting_guard import acquire_close_gate

# ---------------------------------------------------------------- helpers


def module_enabled() -> bool:
	return bool(cint(frappe.db.get_single_value(SETTINGS_DOCTYPE, "enabled")))


def require_enabled() -> None:
	if not module_enabled():
		frappe.throw(
			_("Monthly Closing is disabled in Monthly Close Settings. Existing locks stay in force."),
			title=_("Module Disabled"),
		)


def get_policy(company: str):
	if not frappe.db.exists(POLICY_DOCTYPE, company):
		frappe.throw(
			_("Company {0} has no Monthly Close Policy. Create one to start managing its closes.").format(
				frappe.bold(company)
			),
			title=_("No Close Policy"),
		)

	policy = frappe.get_doc(POLICY_DOCTYPE, company)
	if not policy.enabled:
		frappe.throw(_("The Monthly Close Policy for {0} is disabled.").format(frappe.bold(company)))
	return policy


def lock_close(name: str):
	"""FOR UPDATE on the close row, then a fresh load of the document."""
	acquire_close_gate(name)
	close = frappe.get_doc(CLOSE_DOCTYPE, name)
	require_company_access(close.company)
	return close


def set_state(close, to_state: str, event_type: str, details: dict | None = None, **values):
	from_state = close.state
	close.update(values)
	close.state = to_state
	with service_write():
		close.save(ignore_permissions=True)
	log_event(close, event_type, from_state=from_state, to_state=to_state, details=details)
	return close


def require_state(close, *states: str) -> None:
	if close.state not in states:
		frappe.throw(
			_("{0} is {1}. This action needs it to be {2}.").format(
				frappe.bold(close.name), _(close.state), " / ".join(_(s) for s in states)
			),
			title=_("Not Allowed in This State"),
		)


def current_tasks(close) -> list:
	return frappe.get_all(
		TASK_DOCTYPE,
		filters={"monthly_close": close.name, "revision": close.revision},
		fields=["name", "title", "status", "is_mandatory", "evidence_required", "evidence", "task_key"],
		order_by="idx asc, creation asc",
	)


def incomplete_mandatory_tasks(close) -> list:
	return [
		task
		for task in current_tasks(close)
		if task.is_mandatory and (task.status != "Done" or (task.evidence_required and not task.evidence))
	]


def latest_run(close):
	if not close.latest_check_run:
		return None
	run = frappe.get_doc(CHECK_RUN_DOCTYPE, close.latest_check_run)
	if run.revision != close.revision or run.policy_version != close.policy_version:
		return None
	return run


def require_fresh_run(close):
	"""The latest completed run must describe the ledgers as they are right now."""
	run = latest_run(close)
	if not run or run.status != RUN_COMPLETED:
		frappe.throw(
			_("Run the close checks for revision {0} and wait for them to complete first.").format(
				close.revision
			),
			title=_("Checks Required"),
		)

	current_policy = cint(frappe.db.get_value(POLICY_DOCTYPE, close.company, "policy_version"))
	if current_policy != cint(run.policy_version):
		frappe.throw(
			_("The close policy changed (version {0} to {1}) after {2} ran. Run the checks again.").format(
				run.policy_version, current_policy, frappe.bold(run.name)
			),
			title=_("Stale Checks"),
		)

	digest, components = fingerprint.compute(close)
	if digest != run.fingerprint:
		changed = fingerprint.changed_components(run.fingerprint_components, components)
		frappe.throw(
			_("The checks in {0} are stale: {1} changed since they ran. Run the checks again.").format(
				frappe.bold(run.name), ", ".join(changed) or _("financial data")
			),
			title=_("Stale Checks"),
		)
	return run


def require_no_blockers(run, close) -> None:
	blocking = blocking_results(run, close)
	if blocking:
		frappe.throw(
			_("These checks block the close: {0}. Resolve them or get an approved exception.").format(
				", ".join(f"{row.check_label} ({_(row.status)})" for row in blocking)
			),
			title=_("Blocking Findings"),
		)


# ---------------------------------------------------------------- creation


def create_close(company: str, period_start, template: str | None = None) -> str:
	require_enabled()
	require_preparer()
	require_company_access(company)

	policy = get_policy(company)
	start, _end = month_bounds(period_start)
	if policy.cutover_period and start < getdate(policy.cutover_period):
		frappe.throw(
			_("{0} is before the first managed month {1} in the policy for {2}.").format(
				month_label(start), month_label(policy.cutover_period), company
			),
			title=_("Before Cutover"),
		)

	close = frappe.get_doc(
		{
			"doctype": CLOSE_DOCTYPE,
			"company": company,
			"period_start": start,
			"template": template or policy.template,
		}
	)
	try:
		close.insert()
	except frappe.DuplicateEntryError, frappe.UniqueValidationError:
		frappe.throw(
			_(
				"A Monthly Close for {0} {1} already exists. There is one close per company and month; history is kept as revisions."
			).format(company, month_label(start)),
			frappe.DuplicateEntryError,
			title=_("Close Exists"),
		)

	log_event(close, "Created", to_state=DRAFT)
	return close.name


def snapshot_template(template_name: str) -> tuple[str, int]:
	template = frappe.get_doc(TEMPLATE_DOCTYPE, template_name)
	tasks = [
		{
			"task_key": row.task_key,
			"title": row.title,
			"category": row.category,
			"description": row.description,
			"is_mandatory": cint(row.is_mandatory),
			"evidence_required": cint(row.evidence_required),
			"manual_certification": cint(row.manual_certification),
			"depends_on": row.depends_on,
			"assigned_role": row.assigned_role,
			"due_offset_days": cint(row.due_offset_days),
		}
		for row in template.tasks
	]
	return json.dumps(
		{"template": template.name, "version": template.version, "tasks": tasks}, indent=1
	), cint(template.version)


def create_tasks(close) -> int:
	"""Copy the frozen template snapshot into Task documents for the current revision."""
	snapshot = json.loads(close.template_snapshot or "{}")
	created = 0
	for idx, row in enumerate(snapshot.get("tasks", []), start=1):
		task = frappe.get_doc(
			{
				"doctype": TASK_DOCTYPE,
				"monthly_close": close.name,
				"company": close.company,
				"revision": close.revision,
				"idx": idx,
				"task_key": row["task_key"],
				"title": row["title"],
				"category": row.get("category"),
				"description": row.get("description"),
				"is_mandatory": row.get("is_mandatory"),
				"evidence_required": row.get("evidence_required"),
				"manual_certification": row.get("manual_certification"),
				"depends_on": row.get("depends_on"),
				"assigned_role": row.get("assigned_role"),
				"due_date": add_days(close.period_end, cint(row.get("due_offset_days"))),
				"status": "Open",
			}
		)
		with service_write():
			task.insert(ignore_permissions=True)
		created += 1
	return created


# ---------------------------------------------------------------- transitions


def start_close(name: str) -> None:
	require_enabled()
	require_preparer()
	close = lock_close(name)
	require_state(close, DRAFT)

	policy = get_policy(close.company)
	if not close.template:
		frappe.throw(_("Pick a checklist template before starting the close."))

	snapshot, template_version = snapshot_template(close.template)
	close.template_snapshot = snapshot
	close.template_version = template_version
	close.policy_version = policy.policy_version
	close.preparer = close.preparer or frappe.session.user
	set_state(
		close,
		IN_PROGRESS,
		"Started",
		details={"template": close.template, "template_version": template_version},
	)
	count = create_tasks(close)
	frappe.msgprint(_("{0} checklist tasks created.").format(count), alert=True, indicator="green")


def resume_close(name: str) -> None:
	"""Reopened -> In Progress. A fresh task list is created for the new revision from the original snapshot."""
	require_enabled()
	require_preparer()
	close = lock_close(name)
	require_state(close, REOPENED)
	policy = get_policy(close.company)
	set_state(close, IN_PROGRESS, "Resumed", policy_version=policy.policy_version)
	create_tasks(close)


def request_check_run(name: str) -> str:
	require_enabled()
	require_role(ROLE_PREPARER, ROLE_REVIEWER, ROLE_MANAGER)
	close = lock_close(name)
	require_state(close, IN_PROGRESS, READY_FOR_REVIEW, REOPENED)

	# Use the policy as it is now; a newer policy version invalidates older runs.
	policy = get_policy(close.company)
	if policy.policy_version != close.policy_version:
		with service_write():
			close.db_set("policy_version", policy.policy_version)

	pending = frappe.db.exists(
		CHECK_RUN_DOCTYPE,
		{"monthly_close": close.name, "revision": close.revision, "status": ["in", ["Queued", "Running"]]},
	)
	if pending:
		frappe.throw(
			_("Checks are already running in {0}.").format(frappe.bold(pending)), title=_("Already Running")
		)

	run_name = enqueue_check_run(close)
	log_event(close, "Checks Requested", reference_doctype=CHECK_RUN_DOCTYPE, reference_name=run_name)
	return run_name


def submit_for_review(name: str) -> None:
	require_enabled()
	require_preparer()
	close = lock_close(name)
	require_state(close, IN_PROGRESS)

	missing = incomplete_mandatory_tasks(close)
	if missing:
		frappe.throw(
			_("Complete these mandatory tasks, with evidence where required: {0}").format(
				", ".join(task.title for task in missing)
			),
			title=_("Checklist Incomplete"),
		)

	run = require_fresh_run(close)
	require_no_blockers(run, close)

	set_state(
		close,
		READY_FOR_REVIEW,
		"Submitted for Review",
		details={"check_run": run.name, "fingerprint": run.fingerprint},
		submitted_by=frappe.session.user,
		submitted_at=now_datetime(),
		submitted_check_run=run.name,
	)
	subject, message = transition_message(close, "Submitted for Review")
	notify_after_commit(
		close, subject, message, users=[close.reviewer], roles=[] if close.reviewer else [ROLE_REVIEWER]
	)


def approve(name: str, comment: str | None = None) -> None:
	require_enabled()
	require_reviewer()
	close = lock_close(name)
	require_state(close, READY_FOR_REVIEW)
	policy = get_policy(close.company)

	self_approved = require_different_actor(
		close.submitted_by, cint(policy.allow_self_approval), _("a close")
	)
	self_approved = (
		require_different_actor(close.preparer, cint(policy.allow_self_approval), _("a close"))
		or self_approved
	)

	run = require_fresh_run(close)
	if run.name != close.submitted_check_run:
		# A newer run exists; it must be the one the preparer submitted for review.
		frappe.throw(
			_("Checks were re-run after submission. Send the close back and resubmit it."),
			title=_("Review Out of Date"),
		)
	require_no_blockers(run, close)

	set_state(
		close,
		APPROVED,
		"Approved",
		details={
			"revision": close.revision,
			"check_run": run.name,
			"fingerprint": run.fingerprint,
			"self_approval_policy_used": self_approved,
			"comment": comment,
		},
		approved_by=frappe.session.user,
		approved_at=now_datetime(),
		approved_revision=close.revision,
		approved_check_run=run.name,
		approved_fingerprint=run.fingerprint,
		self_approval_used=cint(close.self_approval_used or self_approved),
	)
	subject, message = transition_message(close, "Approved", comment)
	notify_after_commit(close, subject, message, roles=[ROLE_MANAGER])


def reject(name: str, reason: str) -> None:
	"""Ready for Review / Approved -> In Progress, with a recorded reason."""
	require_enabled()
	require_reviewer()
	if not (reason or "").strip():
		frappe.throw(_("A reason is required to send a close back."))

	close = lock_close(name)
	require_state(close, READY_FOR_REVIEW, APPROVED)
	invalidate_approval(close, "Sent Back", reason)
	subject, message = transition_message(close, "Sent Back", reason)
	notify_after_commit(close, subject, message, users=[close.preparer, close.submitted_by])


def invalidate_approval(close, event_type: str, reason: str) -> None:
	set_state(
		close,
		IN_PROGRESS,
		event_type,
		details={"reason": reason, "previous_approval": close.approved_check_run},
		rejection_reason=reason,
		submitted_by=None,
		submitted_at=None,
		submitted_check_run=None,
		approved_by=None,
		approved_at=None,
		approved_revision=0,
		approved_check_run=None,
		approved_fingerprint=None,
	)


# ---------------------------------------------------------------- waivers


def request_waiver(
	close_name: str, result_row: str, explanation: str, evidence: str | None = None, expires_on=None
) -> str:
	require_enabled()
	require_preparer()
	if not (explanation or "").strip():
		frappe.throw(_("Explain why the finding is acceptable."))

	close = lock_close(close_name)
	require_state(close, IN_PROGRESS)

	row = frappe.get_doc("Monthly Close Check Result", result_row)
	run = frappe.get_doc(CHECK_RUN_DOCTYPE, row.parent)
	if run.monthly_close != close.name or run.revision != close.revision:
		frappe.throw(_("That finding does not belong to the current revision of this close."))

	if row.status not in (BLOCKER, WARNING):
		frappe.throw(
			_("Only Warning and Blocker findings can be waived. {0} results must be fixed.").format(
				_(row.status)
			)
		)

	waiver = frappe.get_doc(
		{
			"doctype": EXCEPTION_DOCTYPE,
			"monthly_close": close.name,
			"company": close.company,
			"revision": close.revision,
			"check_run": run.name,
			"check_result": row.name,
			"check_id": row.check_id,
			"check_label": row.check_label,
			"finding_status": row.status,
			"finding_signature": row.finding_signature,
			"finding_message": row.message,
			"explanation": explanation,
			"evidence": evidence,
			"expires_on": expires_on,
			"status": REQUESTED,
			"requested_by": frappe.session.user,
		}
	)
	with service_write():
		waiver.insert(ignore_permissions=True)
	log_event(close, "Exception Requested", reference_doctype=EXCEPTION_DOCTYPE, reference_name=waiver.name)
	return waiver.name


def decide_waiver(name: str, approve_it: bool, note: str | None = None) -> None:
	require_enabled()
	require_reviewer()
	waiver = frappe.get_doc(EXCEPTION_DOCTYPE, name)
	close = lock_close(waiver.monthly_close)
	require_state(close, IN_PROGRESS, READY_FOR_REVIEW)

	waiver = frappe.get_doc(EXCEPTION_DOCTYPE, name, for_update=True)
	if waiver.status != REQUESTED:
		frappe.throw(_("Exception {0} is already {1}.").format(name, _(waiver.status)))
	if waiver.revision != close.revision:
		frappe.throw(_("Exception {0} belongs to an earlier revision.").format(name))
	if not approve_it and not (note or "").strip():
		frappe.throw(_("A reason is required to reject an exception."))

	policy = get_policy(close.company)
	self_approved = approve_it and require_different_actor(
		waiver.requested_by, cint(policy.allow_self_approval), _("an exception")
	)

	waiver.status = REQUEST_APPROVED if approve_it else REQUEST_REJECTED
	waiver.decided_by = frappe.session.user
	waiver.decided_at = now_datetime()
	waiver.decision_note = note
	waiver.self_approval_used = cint(self_approved)
	with service_write():
		waiver.save(ignore_permissions=True)

	log_event(
		close,
		"Exception Approved" if approve_it else "Exception Rejected",
		details={"note": note, "self_approval_policy_used": self_approved},
		reference_doctype=EXCEPTION_DOCTYPE,
		reference_name=waiver.name,
	)

	# An approved close was approved against a different set of waivers.
	if close.state == READY_FOR_REVIEW and not approve_it:
		invalidate_approval(close, "Sent Back", _("Exception {0} was rejected.").format(waiver.name))


# ---------------------------------------------------------------- bank certification


def certify_bank_account(name: str) -> None:
	require_enabled()
	require_reviewer()
	cert = frappe.get_doc("Monthly Close Bank Certification", name)
	close = lock_close(cert.monthly_close)
	require_state(close, IN_PROGRESS)

	cert = frappe.get_doc("Monthly Close Bank Certification", name, for_update=True)
	if cert.revision != close.revision:
		frappe.throw(_("This workpaper belongs to an earlier revision."))
	if not cert.statement_date or not cert.statement_file:
		frappe.throw(_("Statement date and the statement itself must be attached before certifying."))

	policy = get_policy(close.company)
	require_different_actor(
		cert.prepared_by or cert.owner, cint(policy.allow_self_approval), _("a bank workpaper")
	)
	cert.refresh_balances()
	if cert.unexplained_difference and not (cert.difference_explanation or "").strip():
		frappe.throw(_("Explain the difference before certifying."))

	cert.status = "Certified"
	cert.certified_by = frappe.session.user
	cert.certified_at = now_datetime()
	with service_write():
		cert.save(ignore_permissions=True)
	log_event(close, "Bank Certified", reference_doctype=cert.doctype, reference_name=cert.name)


# ---------------------------------------------------------------- external Accounting Period


def associate_external_period(name: str, accounting_period: str | None) -> None:
	"""System Manager confirms that an existing exact-range period is the lock for this close."""
	require_role("System Manager")
	close = lock_close(name)
	if close.state in LOCKED_STATES:
		frappe.throw(_("The lock of a closed month cannot be changed. Reopen it first."))

	if accounting_period:
		from erpcore.erp_core.monthly_close import native_lock

		close.external_accounting_period = accounting_period
		assessment = native_lock.assess(close)
		if assessment.kind != native_lock.ASSOCIATED:
			frappe.throw(
				_("{0} cannot be associated: {1}").format(
					accounting_period, " ".join(assessment.problems) or _(assessment.kind)
				)
			)

	with service_write():
		close.db_set("external_accounting_period", accounting_period or None)
	log_event(
		close,
		"External Lock Associated" if accounting_period else "External Lock Removed",
		details={"accounting_period": accounting_period},
	)


def detach_owned_lock(name: str) -> None:
	"""Hand an owned period over to native ERPNext, still enabled. Used before uninstalling."""
	require_role("System Manager")
	close = lock_close(name)
	if not (close.lock_owned and close.accounting_period):
		frappe.throw(_("This close does not own an Accounting Period."))

	from erpcore.erp_core.monthly_close.constants import OWNER_FIELD
	from erpcore.erp_core.monthly_close.native_lock import lock_service

	period = frappe.get_doc("Accounting Period", close.accounting_period, for_update=True)
	period.set(OWNER_FIELD, None)
	with lock_service():
		period.save(ignore_permissions=True)

	with service_write():
		close.db_set({"lock_owned": 0, "external_accounting_period": period.name})
	log_event(
		close,
		"Lock Detached",
		details={"accounting_period": period.name, "disabled": period.disabled},
	)
