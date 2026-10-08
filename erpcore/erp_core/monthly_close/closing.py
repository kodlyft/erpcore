# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Hard close: lock the month, then seal the evidence packet.

The request endpoint only validates and enqueues; it never locks or evaluates in
the user's request transaction. That transaction has usually opened its
REPEATABLE READ view before the user clicked, so it could miss a posting that
committed a moment earlier.

The worker runs two transactions.

Transaction 1 (lock):
* commit, so no earlier read view survives;
* SELECT ... FOR UPDATE on the close row. In-flight postings into the month hold
  a shared lock on that row (see posting_guard), so this waits for them to
  commit, and later postings wait for this transaction;
* recheck state, request token, approved revision, month end, sequence,
  mandatory tasks and waivers;
* rerun every check synchronously (the first plain read here opens a read view
  that includes everything committed);
* compare the fingerprint with the approved one;
* establish the owned native Accounting Period;
* set state Closing, append the event, commit.
Any failure rolls all of it back, so no Closing/Closed state or new active lock
is left behind.

Transaction 2 (seal):
* postings are already refused, so the reports describe a frozen month;
* generate the snapshots, the revision manifest and the readable packet,
  recheck fingerprint and lock health;
* set Closed and commit; notify after commit.
If it fails, the close stays Closing: the lock stays in force (the safe side).
A Close Manager can Retry Seal or Abort Close, which releases only the
owned lock and returns the close to Approved.

Durable job state and recovery (docs/monthly-closing/job-recovery.md):

* The close records the pending action, a fencing token, the RQ job id, the
  attempt number and when it was requested and started. Every step re-checks
  the token under the close lock, so a worker whose token was replaced
  (retry, recovery, abort) writes nothing.
* Before running, each stage re-authorises the requester as of now: module and
  policy enabled, Close Manager role and company access. A revoked requester's
  queued hard close is refused; a revoked requester's seal is refused but the
  lock stays (the safe side).
* A worker killed outside normal exception handling leaves the pending action
  set. `recover_stalled_closes` (scheduled, and `retry_*` on demand) treats a
  stage as dead only when its RQ job is neither queued nor running and the
  stage has been pending longer than PENDING_LEASE_SECONDS. A dead lock stage
  is cleared (its transaction never committed, so there is no lock to undo);
  a dead seal stage becomes Seal Failed, keeping the lock, for a manager to
  retry or abort. Nothing ever unlocks on a timeout.
"""

import uuid

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate, now_datetime

from erpcore.erp_core.monthly_close import native_lock, snapshot, txn
from erpcore.erp_core.monthly_close.check_runner import blocking_results, run_checks_sync, service_write
from erpcore.erp_core.monthly_close.constants import (
	APPROVED,
	CLOSE_DOCTYPE,
	CLOSED,
	CLOSING,
	ROLE_MANAGER,
	ROLE_PREPARER,
	ROLE_REVIEWER,
)
from erpcore.erp_core.monthly_close.events import log_event
from erpcore.erp_core.monthly_close.fingerprint import FINANCIAL_COMPONENTS, changed_components
from erpcore.erp_core.monthly_close.lifecycle import (
	get_policy,
	incomplete_mandatory_tasks,
	lock_close,
	module_enabled,
	require_enabled,
	require_state,
	set_state,
)
from erpcore.erp_core.monthly_close.notifications import (
	notify_after_commit,
	publish_close_update,
	transition_message,
)
from erpcore.erp_core.monthly_close.periods import assert_month_ended, month_label, previous_month_start
from erpcore.erp_core.monthly_close.permissions import has_company_access, require_manager

PENDING_LOCK = "Hard Close"
PENDING_SEAL = "Seal Packet"
SEAL_FAILED = "Seal Failed"

JOB_TIMEOUT = 3600
PENDING_LEASE_SECONDS = JOB_TIMEOUT + 600


class HardCloseRefused(frappe.ValidationError):
	pass


def fail_point(stage: str) -> None:
	"""Failure injection for tests only."""
	in_test = getattr(frappe, "in_test", False) or frappe.flags.in_test
	if in_test and frappe.flags.get("erpcore_close_fail_at") == stage:
		raise RuntimeError(f"Injected failure at {stage}")


def refuse(message: str):
	frappe.throw(message, HardCloseRefused, title=_("Hard Close Refused"))


def preflight(close) -> list[str]:
	"""What would stop a hard close right now. Shown in the confirmation dialog; the job rechecks all of it."""
	problems = []
	if close.state != APPROVED:
		problems.append(_("The close is {0}, not Approved.").format(_(close.state)))
	if cint(close.approved_revision) != cint(close.revision):
		problems.append(
			_("The approval is for revision {0}, not {1}.").format(close.approved_revision, close.revision)
		)
	if getdate(close.period_end) >= getdate(frappe.utils.nowdate()):
		problems.append(_("The month has not ended."))
	problems += [_("Mandatory task open: {0}").format(t.title) for t in incomplete_mandatory_tasks(close)]
	problems += sequence_problems(close)

	assessment = native_lock.assess(close)
	if assessment.kind in (native_lock.CONFLICT, native_lock.COMPATIBLE):
		problems += assessment.problems or [
			_("Accounting Period {0} must be associated by a System Manager.").format(
				", ".join(assessment.external)
			)
		]
	return problems


def sequence_problems(close) -> list[str]:
	"""After the cutover, months close in order."""
	policy_cutover = frappe.db.get_value("Monthly Close Policy", close.company, "cutover_period")
	previous = previous_month_start(close.period_start)
	if policy_cutover and previous < getdate(policy_cutover):
		return []

	state = frappe.db.get_value(CLOSE_DOCTYPE, {"company": close.company, "period_start": previous}, "state")
	if state != CLOSED:
		return [
			_("{0} must be closed first (it is {1}).").format(
				month_label(previous), _(state or "not started")
			)
		]
	return []


def request_hard_close(name: str) -> None:
	require_enabled()
	require_manager()
	close = lock_close(name)
	require_state(close, APPROVED)
	assert_month_ended(close.period_end)

	if close.pending_action:
		if not stage_is_dead(close):
			frappe.throw(_("{0} is already in progress for this close.").format(_(close.pending_action)))
		log_event(close, "Hard Close Recovered", details={"stale_job": close.pending_job_id})

	problems = preflight(close)
	if problems:
		refuse(" ".join(problems))

	enqueue_stage(close, PENDING_LOCK, "execute_hard_close", "hard-close")
	log_event(close, "Hard Close Requested", details={"job_id": close.pending_job_id})


def enqueue_stage(close, action: str, method: str, label: str) -> str:
	"""Record a new pending stage with a fresh fencing token and enqueue its worker after commit."""
	token = uuid.uuid4().hex
	job_id = f"monthly-close-{label}::{close.name}::{token}"
	values = {
		"pending_action": action,
		"pending_token": token,
		"pending_job_id": job_id,
		"pending_attempt": cint(close.pending_attempt) + 1,
		"pending_requested_at": now_datetime(),
		"pending_started_at": None,
		"last_error": None,
	}
	if action == PENDING_LOCK:
		values["pending_requested_by"] = frappe.session.user
	with service_write():
		close.db_set(values)
	frappe.enqueue(
		f"erpcore.erp_core.monthly_close.closing.{method}",
		queue="long",
		timeout=JOB_TIMEOUT,
		job_id=job_id,
		enqueue_after_commit=True,
		close_name=close.name,
		token=token,
	)
	return token


def stage_is_dead(close) -> bool:
	"""True only when the pending stage's job is gone and it has been pending past the lease."""
	if close.pending_action not in (PENDING_LOCK, PENDING_SEAL):
		return False
	since = close.pending_started_at or close.pending_requested_at
	if since and get_datetime(since) > add_to_date(now_datetime(), seconds=-PENDING_LEASE_SECONDS):
		return False
	if close.pending_job_id:
		from frappe.utils.background_jobs import is_job_enqueued

		try:
			if is_job_enqueued(close.pending_job_id):
				return False
		except Exception:
			# Queue unreachable: we cannot prove the worker is dead.
			return False
	return True


def requester_problems(close) -> list[str]:
	"""Re-authorise the requester at execution time, not only when they clicked."""
	user = close.pending_requested_by
	problems = []
	if not module_enabled():
		problems.append(_("Monthly Closing was disabled after the request."))
	if not cint(frappe.db.get_value("Monthly Close Policy", close.company, "enabled")):
		problems.append(_("The close policy was disabled after the request."))
	if not user:
		problems.append(_("The request has no requester."))
		return problems
	enabled = frappe.db.get_value("User", user, "enabled")
	roles = set(frappe.get_roles(user))
	if user != "Administrator" and (not enabled or not (ROLE_MANAGER in roles or "System Manager" in roles)):
		problems.append(_("{0} no longer holds the Close Manager role.").format(user))
	if not has_company_access(close.company, user):
		problems.append(_("{0} no longer has access to {1}.").format(user, close.company))
	return problems


def claim_stage(close_name: str, token: str, action: str, state: str) -> bool:
	"""Record that a worker started this stage. False when the token is no longer current."""
	txn.begin("mc_claim")
	close = frappe.get_doc(CLOSE_DOCTYPE, close_name, for_update=True)
	if close.pending_token != token or close.pending_action != action or close.state != state:
		txn.rollback("mc_claim")
		return False
	with service_write():
		close.db_set("pending_started_at", now_datetime(), update_modified=False)
	txn.commit()
	return True


def execute_hard_close(close_name: str, token: str) -> None:
	"""Worker entry point. Transaction boundaries are described in the module docstring."""
	if not claim_stage(close_name, token, PENDING_LOCK, APPROVED):
		return

	txn.begin("mc_lock")
	try:
		close = frappe.get_doc(CLOSE_DOCTYPE, close_name, for_update=True)
		if close.pending_token != token or close.pending_action != PENDING_LOCK or close.state != APPROVED:
			txn.rollback("mc_lock")
			return

		revoked = requester_problems(close)
		if revoked:
			refuse(" ".join(revoked))

		lock_month(close)
		txn.commit()
	except Exception as exc:
		txn.rollback("mc_lock")
		record_failure(close_name, token, "Hard Close Failed", exc, clear_pending=True)
		return

	seal(close_name, token)


def lock_month(close) -> None:
	"""Transaction 1 body. The caller holds FOR UPDATE on the close row."""
	if cint(close.approved_revision) != cint(close.revision):
		refuse(_("The approval does not cover the current revision."))

	assert_month_ended(close.period_end)

	problems = sequence_problems(close)
	missing = incomplete_mandatory_tasks(close)
	if problems or missing:
		refuse(" ".join(problems + [_("Mandatory task open: {0}").format(t.title) for t in missing]))

	policy = get_policy(close.company)
	if cint(policy.policy_version) != cint(close.policy_version):
		refuse(_("The close policy changed after approval. Send the close back for review."))

	run = run_checks_sync(close, policy, purpose="Final")
	if run.fingerprint != close.approved_fingerprint:
		approved = frappe.db.get_value(
			"Monthly Close Check Run", close.approved_check_run, "fingerprint_components"
		)
		changed = changed_components(approved, frappe.parse_json(run.fingerprint_components))
		refuse(
			_(
				"Financial data changed after approval ({0}). Send the close back, rerun checks and approve again."
			).format(", ".join(changed) or _("unknown"))
		)

	blocking = blocking_results(run, close)
	if blocking:
		refuse(_("Final checks found blockers: {0}.").format(", ".join(r.check_label for r in blocking)))

	fail_point("lock")
	period, owned = native_lock.establish(close)

	fail_point("event")
	set_state(
		close,
		CLOSING,
		"Lock Established",
		details={
			"accounting_period": period,
			"owned": owned,
			"final_check_run": run.name,
			"fingerprint": run.fingerprint,
		},
		accounting_period=period,
		lock_owned=cint(owned),
		closing_check_run=run.name,
		closing_fingerprint=run.fingerprint,
		closer=close.pending_requested_by,
		pending_action=PENDING_SEAL,
	)
	fail_point("state")


def seal(close_name: str, token: str) -> None:
	"""Transaction 2: capture the packet of a locked month and mark it Closed."""
	if not claim_stage(close_name, token, PENDING_SEAL, CLOSING):
		return

	txn.begin("mc_seal")
	try:
		close = frappe.get_doc(CLOSE_DOCTYPE, close_name, for_update=True)
		if close.state != CLOSING or close.pending_token != token:
			txn.rollback("mc_seal")
			return

		revoked = requester_problems(close)
		if revoked:
			raise frappe.ValidationError(
				" ".join(revoked) + " " + _("The month stays locked; a Close Manager can retry or abort.")
			)

		health = native_lock.lock_health(close)
		if health["status"] != "Healthy":
			raise frappe.ValidationError(" ".join(health["problems"]))

		from erpcore.erp_core.monthly_close import fingerprint

		digest, components = fingerprint.compute(close)
		if digest != close.closing_fingerprint:
			raise frappe.ValidationError(
				_(
					"Financial data changed while the month was locked ({0}). Investigate before sealing."
				).format(
					", ".join(
						changed_components(
							frappe.db.get_value(
								"Monthly Close Check Run", close.closing_check_run, "fingerprint_components"
							),
							components,
						)
					)
				)
			)

		fail_point("snapshot")
		names = snapshot.capture(close)

		closed_at = now_datetime()
		close.closed_by = close.pending_requested_by
		close.closed_at = closed_at
		fail_point("packet")
		packet = snapshot.seal_revision_packet(close, names)

		set_state(
			close,
			CLOSED,
			"Closed",
			details={"snapshots": names, "fingerprint": digest, "packet": packet},
			closed_by=close.closed_by,
			closed_at=closed_at,
			pending_action=None,
			pending_token=None,
			pending_requested_by=None,
			pending_job_id=None,
			pending_requested_at=None,
			pending_started_at=None,
			last_error=None,
			revalidation_required=0,
		)
		subject, message = transition_message(close, "Closed")
		notify_after_commit(close, subject, message, roles=[ROLE_MANAGER, ROLE_PREPARER, ROLE_REVIEWER])
		txn.commit()
	except Exception as exc:
		txn.rollback("mc_seal")
		record_failure(close_name, token, "Seal Failed", exc, clear_pending=False)


def record_failure(close_name: str, token: str, event_type: str, exc: Exception, clear_pending: bool) -> None:
	"""Record why a step failed, in its own transaction, without touching state or locks."""
	if not isinstance(exc, frappe.ValidationError):
		frappe.log_error(title=f"erpcore: {event_type} for {close_name}")

	txn.begin("mc_record_failure")
	try:
		close = frappe.get_doc(CLOSE_DOCTYPE, close_name, for_update=True)
		if close.pending_token != token:
			txn.rollback("mc_record_failure")
			return

		message = frappe.utils.strip_html(str(exc))[:2000]
		values = {"last_error": message}
		if clear_pending:
			values.update(
				{
					"pending_action": None,
					"pending_token": None,
					"pending_requested_by": None,
					"pending_job_id": None,
					"pending_started_at": None,
				}
			)
		else:
			# The token is retired: only a new Retry Seal can resume, never the failed worker.
			values.update({"pending_action": SEAL_FAILED, "pending_token": None, "pending_job_id": None})

		with service_write():
			close.db_set(values)
		log_event(close, event_type, details={"error": message})
		publish_close_update(close_name)
		txn.commit()
	except Exception:
		txn.rollback("mc_record_failure")
		frappe.log_error(title=f"erpcore: could not record {event_type} for {close_name}")


def retry_seal(name: str) -> None:
	"""Closing -> new seal attempt, after a recorded failure or a seal worker presumed dead."""
	require_manager()
	close = lock_close(name)
	require_state(close, CLOSING)
	if close.pending_action == PENDING_SEAL and not stage_is_dead(close):
		frappe.throw(_("Sealing is still running."))

	stale_job = close.pending_job_id
	enqueue_stage(close, PENDING_SEAL, "seal", "seal")
	log_event(close, "Seal Retried", details={"job_id": close.pending_job_id, "stale_job": stale_job})


def recover_stalled_closes() -> None:
	"""Scheduled: turn stages whose worker died into recorded, retryable failures. Never unlocks."""
	for name in frappe.get_all(
		CLOSE_DOCTYPE, filters={"pending_action": ["in", [PENDING_LOCK, PENDING_SEAL]]}, pluck="name"
	):
		txn.begin("mc_recover")
		try:
			close = frappe.get_doc(CLOSE_DOCTYPE, name, for_update=True)
			if not stage_is_dead(close):
				txn.rollback("mc_recover")
				continue

			message = _("The {0} worker stopped without finishing (job {1}).").format(
				_(close.pending_action), close.pending_job_id or _("unknown")
			)
			if close.pending_action == PENDING_LOCK and close.state == APPROVED:
				values = {
					"pending_action": None,
					"pending_token": None,
					"pending_job_id": None,
					"pending_started_at": None,
					"last_error": message,
				}
				event = "Hard Close Failed"
			elif close.pending_action == PENDING_SEAL and close.state == CLOSING:
				values = {
					"pending_action": SEAL_FAILED,
					"pending_token": None,
					"pending_job_id": None,
					"last_error": message + " " + _("The month stays locked."),
				}
				event = "Seal Failed"
			else:
				txn.rollback("mc_recover")
				continue

			with service_write():
				close.db_set(values)
			log_event(close, event, details={"error": message, "recovered": True})
			publish_close_update(name)
			txn.commit()
		except Exception:
			txn.rollback("mc_recover")
			frappe.log_error(title=f"erpcore: could not recover close {name}")


def abort_close(name: str, reason: str) -> None:
	"""Closing -> Approved. Releases only the lock this close owns."""
	require_manager()
	if not (reason or "").strip():
		frappe.throw(_("A reason is required to abort a close."))

	close = lock_close(name)
	require_state(close, CLOSING)
	released = native_lock.release(close)
	set_state(
		close,
		APPROVED,
		"Close Aborted",
		details={"reason": reason, "released_owned_lock": released},
		pending_action=None,
		pending_token=None,
		pending_requested_by=None,
		pending_job_id=None,
		pending_started_at=None,
		last_error=None,
		closing_check_run=None,
		closing_fingerprint=None,
	)


def revalidation_problems(close) -> list[str]:
	"""Why a later Closed month cannot be confirmed yet after an earlier month was reopened."""
	problems = []
	cutover = frappe.db.get_value("Monthly Close Policy", close.company, "cutover_period")
	filters = {"company": close.company, "period_start": ["<", close.period_start]}
	if cutover:
		# Months before the cutover are not managed by closes.
		filters["period_start"] = ["between", [getdate(cutover), add_days(close.period_start, -1)]]
	earlier = frappe.get_all(
		CLOSE_DOCTYPE,
		filters=filters,
		fields=["name", "state", "month", "revalidation_required"],
		order_by="period_start asc",
	)
	for row in earlier:
		if row.state != CLOSED:
			problems.append(_("{0} is {1}; it must be reclosed first.").format(row.month, _(row.state)))
		elif row.revalidation_required:
			problems.append(_("{0} must be revalidated first.").format(row.month))
		else:
			health = native_lock.lock_health(frappe.get_doc(CLOSE_DOCTYPE, row.name))
			if health["status"] != "Healthy":
				problems.append(_("The lock of {0} is not healthy.").format(row.month))
	return problems


def confirm_revalidation(name: str, comment: str | None = None) -> None:
	"""A later month stays locked when an earlier one is reopened; this confirms it still holds.

	Requires every earlier month to be Closed, revalidated and healthy, then reruns
	the checks against the locked month (no blocker may appear), compares the
	books with the sealed closing fingerprint and records who certified it.
	Changed balances are never accepted here: the month must be reopened and
	reclosed, which seals a new revision packet.
	"""
	from erpcore.erp_core.monthly_close.permissions import require_different_actor, require_reviewer

	require_reviewer()
	if not (comment or "").strip():
		frappe.throw(_("Record what you reviewed to revalidate this month."))

	close = lock_close(name)
	require_state(close, CLOSED)
	if not close.revalidation_required:
		return

	problems = revalidation_problems(close)
	if problems:
		frappe.throw(" ".join(problems), title=_("Earlier Months Not Ready"))

	policy = get_policy(close.company)
	require_different_actor(
		close.closed_by, cint(policy.allow_self_approval), _("a revalidation of a close you hard-closed")
	)

	run = run_checks_sync(close, policy, purpose="Revalidation")
	closing_components = frappe.db.get_value(
		"Monthly Close Check Run", close.closing_check_run, "fingerprint_components"
	)
	changed = changed_components(
		closing_components, frappe.parse_json(run.fingerprint_components), only=FINANCIAL_COMPONENTS
	)
	if changed:
		frappe.throw(
			_(
				"Balances for this month changed ({0}) after the earlier month was reopened. Reopen and reclose this month."
			).format(", ".join(changed)),
			title=_("Revalidation Failed"),
		)

	blocking = blocking_results(run, close)
	if blocking:
		frappe.throw(
			_("Revalidation checks found blockers: {0}.").format(", ".join(r.check_label for r in blocking)),
			title=_("Revalidation Failed"),
		)

	with service_write():
		close.db_set(
			{
				"revalidation_required": 0,
				"revalidated_by": frappe.session.user,
				"revalidated_at": now_datetime(),
			}
		)
	log_event(
		close,
		"Revalidated",
		details={"check_run": run.name, "fingerprint": run.fingerprint, "comment": comment},
		reference_doctype="Monthly Close Check Run",
		reference_name=run.name,
	)
