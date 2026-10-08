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
* generate the snapshots, recheck fingerprint and lock health;
* set Closed and commit; notify after commit.
If it fails, the close stays Closing: the lock stays in force (the safe side).
A Close Manager can Retry Snapshot or Abort Close, which releases only the
owned lock and returns the close to Approved.
"""

import uuid

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime

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
from erpcore.erp_core.monthly_close.fingerprint import changed_components
from erpcore.erp_core.monthly_close.lifecycle import (
	get_policy,
	incomplete_mandatory_tasks,
	lock_close,
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
from erpcore.erp_core.monthly_close.permissions import require_manager
from erpcore.erp_core.monthly_close.posting_guard import acquire_close_gate

PENDING_LOCK = "Hard Close"
PENDING_SEAL = "Seal Packet"
SEAL_FAILED = "Seal Failed"


class HardCloseRefused(frappe.ValidationError):
	pass


def _fail_point(stage: str) -> None:
	"""Failure injection for tests only."""
	in_test = getattr(frappe, "in_test", False) or frappe.flags.in_test
	if in_test and frappe.flags.get("erpcore_close_fail_at") == stage:
		raise RuntimeError(f"Injected failure at {stage}")


def _refuse(message: str):
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
		frappe.throw(_("{0} is already in progress for this close.").format(_(close.pending_action)))

	problems = preflight(close)
	if problems:
		_refuse(" ".join(problems))

	token = uuid.uuid4().hex
	with service_write():
		close.db_set(
			{
				"pending_action": PENDING_LOCK,
				"pending_token": token,
				"pending_requested_by": frappe.session.user,
				"last_error": None,
			}
		)
	log_event(close, "Hard Close Requested")
	frappe.enqueue(
		"erpcore.erp_core.monthly_close.closing.execute_hard_close",
		queue="long",
		timeout=3600,
		job_id=f"monthly-close-hard-close::{close.name}::{token}",
		enqueue_after_commit=True,
		close_name=close.name,
		token=token,
	)


def execute_hard_close(close_name: str, token: str) -> None:
	"""Worker entry point. Transaction boundaries are described in the module docstring."""
	txn.begin("mc_lock")

	try:
		acquire_close_gate(close_name)
		close = frappe.get_doc(CLOSE_DOCTYPE, close_name)
		if close.pending_token != token or close.pending_action != PENDING_LOCK or close.state != APPROVED:
			txn.rollback("mc_lock")
			return

		lock_month(close)
		txn.commit()
	except Exception as exc:
		txn.rollback("mc_lock")
		_record_failure(close_name, token, "Hard Close Failed", exc, clear_pending=True)
		return

	seal(close_name, token)


def lock_month(close) -> None:
	"""Transaction 1 body. The caller holds FOR UPDATE on the close row."""
	if cint(close.approved_revision) != cint(close.revision):
		_refuse(_("The approval does not cover the current revision."))

	assert_month_ended(close.period_end)

	problems = sequence_problems(close)
	missing = incomplete_mandatory_tasks(close)
	if problems or missing:
		_refuse(" ".join(problems + [_("Mandatory task open: {0}").format(t.title) for t in missing]))

	policy = get_policy(close.company)
	if cint(policy.policy_version) != cint(close.policy_version):
		_refuse(_("The close policy changed after approval. Send the close back for review."))

	run = run_checks_sync(close, policy, purpose="Final")
	if run.fingerprint != close.approved_fingerprint:
		approved = frappe.db.get_value(
			"Monthly Close Check Run", close.approved_check_run, "fingerprint_components"
		)
		changed = changed_components(approved, frappe.parse_json(run.fingerprint_components))
		_refuse(
			_(
				"Financial data changed after approval ({0}). Send the close back, rerun checks and approve again."
			).format(", ".join(changed) or _("unknown"))
		)

	blocking = blocking_results(run, close)
	if blocking:
		_refuse(_("Final checks found blockers: {0}.").format(", ".join(r.check_label for r in blocking)))

	_fail_point("lock")
	period, owned = native_lock.establish(close)

	_fail_point("event")
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
	_fail_point("state")


def seal(close_name: str, token: str) -> None:
	"""Transaction 2: capture the packet of a locked month and mark it Closed."""
	txn.begin("mc_seal")
	try:
		acquire_close_gate(close_name)
		close = frappe.get_doc(CLOSE_DOCTYPE, close_name)
		if close.state != CLOSING or close.pending_token != token:
			txn.rollback("mc_seal")
			return

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

		_fail_point("snapshot")
		names = snapshot.capture(close)

		set_state(
			close,
			CLOSED,
			"Closed",
			details={"snapshots": names, "fingerprint": digest},
			closed_by=close.pending_requested_by,
			closed_at=now_datetime(),
			pending_action=None,
			pending_token=None,
			pending_requested_by=None,
			last_error=None,
			revalidation_required=0,
		)
		subject, message = transition_message(close, "Closed")
		notify_after_commit(close, subject, message, roles=[ROLE_MANAGER, ROLE_PREPARER, ROLE_REVIEWER])
		txn.commit()
	except Exception as exc:
		txn.rollback("mc_seal")
		_record_failure(close_name, token, "Seal Failed", exc, clear_pending=False)


def _record_failure(
	close_name: str, token: str, event_type: str, exc: Exception, clear_pending: bool
) -> None:
	"""Record why a step failed, in its own transaction, without touching state or locks."""
	if not isinstance(exc, frappe.ValidationError):
		frappe.log_error(title=f"erpcore: {event_type} for {close_name}")

	txn.begin("mc_record_failure")
	try:
		acquire_close_gate(close_name)
		close = frappe.get_doc(CLOSE_DOCTYPE, close_name)
		if close.pending_token != token:
			txn.rollback("mc_record_failure")
			return

		message = frappe.utils.strip_html(str(exc))[:2000]
		values = {"last_error": message}
		if clear_pending:
			values.update({"pending_action": None, "pending_token": None, "pending_requested_by": None})
		else:
			values["pending_action"] = SEAL_FAILED

		with service_write():
			close.db_set(values)
		log_event(close, event_type, details={"error": message})
		publish_close_update(close_name)
		txn.commit()
	except Exception:
		txn.rollback("mc_record_failure")
		frappe.log_error(title=f"erpcore: could not record {event_type} for {close_name}")


def retry_seal(name: str) -> None:
	require_manager()
	close = lock_close(name)
	require_state(close, CLOSING)
	if close.pending_action == PENDING_SEAL and not close.last_error:
		frappe.throw(_("Sealing is still running."))

	token = uuid.uuid4().hex
	with service_write():
		close.db_set({"pending_action": PENDING_SEAL, "pending_token": token, "last_error": None})
	log_event(close, "Seal Retried")
	frappe.enqueue(
		"erpcore.erp_core.monthly_close.closing.seal",
		queue="long",
		timeout=3600,
		job_id=f"monthly-close-seal::{close.name}::{token}",
		enqueue_after_commit=True,
		close_name=close.name,
		token=token,
	)


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
		last_error=None,
		closing_check_run=None,
		closing_fingerprint=None,
	)


def confirm_revalidation(name: str) -> None:
	"""A later month stays locked when an earlier one is reopened; this confirms it still holds."""
	require_manager()
	close = lock_close(name)
	require_state(close, CLOSED)
	if not close.revalidation_required:
		return

	from erpcore.erp_core.monthly_close import fingerprint

	digest, components = fingerprint.compute(close)
	if digest != close.closing_fingerprint:
		changed = changed_components(
			frappe.db.get_value("Monthly Close Check Run", close.closing_check_run, "fingerprint_components"),
			components,
		)
		frappe.throw(
			_(
				"Balances for this month changed ({0}) after the earlier month was reopened. Reopen and reclose this month."
			).format(", ".join(changed)),
			title=_("Revalidation Failed"),
		)

	with service_write():
		close.db_set("revalidation_required", 0)
	log_event(close, "Revalidated", details={"fingerprint": digest})
