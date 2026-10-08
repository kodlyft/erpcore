# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Evaluate close checks into durable Check Runs.

Background runs (`enqueue_check_run`) are enqueued only after the requesting
transaction commits. The worker uses its own short transactions:

1. Lock the run row; give up if it is no longer Queued/Running, or if the close
   moved to another revision or policy version (the run is marked Stale).
2. Mark Running and commit, so the UI shows progress and a crash is visible.
3. Evaluate the checks with a fingerprint taken before and after. If the ledger
   changed during evaluation the results are marked Stale rather than trusted.
4. Lock the close row, re-verify revision and policy, write the results and
   commit. An older worker can never overwrite a newer run, a reopened close or
   a changed policy.

Failures are retried up to MAX_ATTEMPTS with the error recorded on the run.

`run_checks_sync` is the authoritative evaluation used inside the hard-close
job, under the close lock, in the caller's transaction.
"""

import hashlib
import json
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from erpcore.erp_core.monthly_close import fingerprint, txn
from erpcore.erp_core.monthly_close.checks.registry import (
	CheckContext,
	get_checks,
	resolve_status,
)
from erpcore.erp_core.monthly_close.constants import (
	BLOCKER,
	CHECK_RUN_DOCTYPE,
	ERROR,
	EXCEPTION_DOCTYPE,
	REQUEST_APPROVED,
	RUN_COMPLETED,
	RUN_FAILED,
	RUN_QUEUED,
	RUN_RUNNING,
	RUN_STALE,
	TRANSITION_FLAG,
	WARNING,
)

MAX_ATTEMPTS = 3


@contextmanager
def service_write():
	previous = frappe.flags.get(TRANSITION_FLAG)
	frappe.flags[TRANSITION_FLAG] = True
	try:
		yield
	finally:
		frappe.flags[TRANSITION_FLAG] = previous


@contextmanager
def as_system_user():
	"""Checks and snapshots describe the whole company, not what one user's permissions show.

	The caller has already been authorised for the close; evaluation then runs as
	Administrator so record-level User Permissions (on Customer, Cost Center...)
	cannot silently shrink the evidence.
	"""
	user = frappe.session.user
	if user == "Administrator":
		yield
		return

	frappe.set_user("Administrator")  # nosemgrep
	try:
		yield
	finally:
		frappe.set_user(user)  # nosemgrep


def check_settings(policy) -> dict[str, frappe._dict]:
	"""Configured severity/tolerance per check. Registered checks absent from the policy run at their defaults."""
	configured = {row.check_id: row for row in (policy.get("checks") or [])}
	settings = {}
	for check_id, definition in get_checks().items():
		row = configured.get(check_id)
		if row and not cint(row.enabled):
			continue
		settings[check_id] = frappe._dict(
			severity=(row.severity if row and row.severity else definition.default_severity),
			tolerance=flt(row.tolerance) if row else 0.0,
		)
	return settings


def finding_signature(check_id: str, status: str, count, amount, samples) -> str:
	"""Identity of a finding, so a waiver applies only to exactly what was reviewed."""
	payload = json.dumps(
		[check_id, status, cint(count), f"{flt(amount, 6):.6f}", samples], sort_keys=True, default=str
	)
	return hashlib.sha256(payload.encode()).hexdigest()


def evaluate(close, policy) -> list[dict]:
	import erpnext

	currency = erpnext.get_company_currency(close.company)
	precision = cint(frappe.get_precision("GL Entry", "debit")) or 2
	sample_limit = cint(frappe.db.get_single_value("Monthly Close Settings", "max_sample_rows")) or 20
	checks = get_checks()
	results = []

	with as_system_user():
		for check_id, config in check_settings(policy).items():
			definition = checks[check_id]
			ctx = CheckContext(
				company=close.company,
				period_start=close.period_start,
				period_end=close.period_end,
				fiscal_year=close.fiscal_year,
				close_name=close.name,
				revision=close.revision,
				currency=currency,
				precision=precision,
				severity=config.severity,
				tolerance=config.tolerance,
				sample_limit=sample_limit,
				policy=policy,
			)

			savepoint = f"mc_check_{check_id}"
			frappe.db.savepoint(savepoint)
			try:
				finding = definition.function(ctx)
				status = resolve_status(finding, config.severity, config.tolerance, definition.uses_tolerance)
				message, count, amount, samples, route = (
					finding.message,
					finding.count,
					finding.amount,
					finding.samples,
					finding.route,
				)
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				frappe.log_error(title=f"erpcore: monthly close check {check_id} failed for {close.name}")
				status, count, amount, samples, route = ERROR, 0, 0.0, [], None
				message = _("The check could not be evaluated: {0}").format(str(exc)[:500])

			results.append(
				{
					"check_id": check_id,
					"check_label": definition.label,
					"check_version": definition.version,
					"severity": config.severity,
					"status": status,
					"message": message,
					"count": cint(count),
					"amount": flt(amount, precision),
					"tolerance": config.tolerance,
					"samples": json.dumps(samples, default=str, indent=1) if samples else None,
					"route": route,
					"evaluated_at": now_datetime(),
					"finding_signature": finding_signature(check_id, status, count, amount, samples),
				}
			)

	return results


def _apply_results(run, results, digest, components):
	run.set("results", [])
	for row in results:
		run.append("results", row)
	run.fingerprint = digest
	run.fingerprint_components = json.dumps(components, indent=1, sort_keys=True)
	run.blockers = sum(1 for r in results if r["status"] == BLOCKER)
	run.warnings = sum(1 for r in results if r["status"] == WARNING)
	run.errors = sum(1 for r in results if r["status"] == ERROR)
	run.finished_at = now_datetime()
	run.status = RUN_COMPLETED


def _new_run(close, purpose: str):
	run = frappe.get_doc(
		{
			"doctype": CHECK_RUN_DOCTYPE,
			"monthly_close": close.name,
			"company": close.company,
			"revision": close.revision,
			"policy_version": close.policy_version,
			"purpose": purpose,
			"status": RUN_QUEUED,
			"requested_by": frappe.session.user,
		}
	)
	with service_write():
		run.insert(ignore_permissions=True)
	return run


def run_checks_sync(close, policy, purpose: str = "Final"):
	"""Evaluate now, in the caller's transaction. Used by the hard-close job under the close lock."""
	run = _new_run(close, purpose)
	run.status = RUN_RUNNING
	run.started_at = now_datetime()
	run.attempts = 1
	digest, components = fingerprint.compute(close)
	_apply_results(run, evaluate(close, policy), digest, components)
	with service_write():
		run.save(ignore_permissions=True)
	return run


def enqueue_check_run(close) -> str:
	run = _new_run(close, "Background")
	run.db_set("job_id", f"monthly-close-checks::{run.name}", update_modified=False)
	frappe.enqueue(
		"erpcore.erp_core.monthly_close.check_runner.execute_check_run",
		queue="long",
		timeout=3600,
		job_id=run.job_id,
		enqueue_after_commit=True,
		run_name=run.name,
	)
	return run.name


def _is_stale(run, close) -> bool:
	return cint(run.revision) != cint(close.revision) or cint(run.policy_version) != cint(
		close.policy_version
	)


def _mark(run_name: str, status: str, error: str | None = None):
	values = {"status": status, "finished_at": now_datetime()}
	if error is not None:
		values["error"] = error[:2000]
	frappe.db.set_value(CHECK_RUN_DOCTYPE, run_name, values, update_modified=False)


def execute_check_run(run_name: str):
	"""Background worker entry point."""
	txn.begin("mc_run_claim")

	run = frappe.get_doc(CHECK_RUN_DOCTYPE, run_name, for_update=True)
	if run.status not in (RUN_QUEUED, RUN_RUNNING):
		txn.rollback("mc_run_claim")
		return

	close = frappe.get_doc("Monthly Close", run.monthly_close)
	if _is_stale(run, close):
		_mark(run_name, RUN_STALE, _("The close changed revision or policy before this run started."))
		txn.commit()
		return

	attempt = cint(run.attempts) + 1
	frappe.db.set_value(
		CHECK_RUN_DOCTYPE,
		run_name,
		{"status": RUN_RUNNING, "started_at": now_datetime(), "attempts": attempt},
		update_modified=False,
	)
	txn.commit()
	txn.begin("mc_run_evaluate")

	try:
		policy = frappe.get_doc("Monthly Close Policy", close.policy)
		before, components = fingerprint.compute(close)
		results = evaluate(close, policy)
		after, _after_components = fingerprint.compute(close)

		# Lock the close before writing so a concurrent reopen/approval cannot interleave.
		frappe.db.sql("select name from `tabMonthly Close` where name = %s for update", close.name)
		close.reload()
		run = frappe.get_doc(CHECK_RUN_DOCTYPE, run_name, for_update=True)

		stale_reason = None
		if run.status != RUN_RUNNING:
			txn.rollback("mc_run_evaluate")
			return
		if _is_stale(run, close):
			stale_reason = _("The close changed revision or policy while checks were running.")
		elif before != after:
			stale_reason = _("Ledgers changed while the checks were running. Run the checks again.")

		if stale_reason:
			txn.rollback("mc_run_evaluate")
			_mark(run_name, RUN_STALE, stale_reason)
			txn.commit()
			return

		_apply_results(run, results, before, components)
		with service_write():
			run.save(ignore_permissions=True)
			frappe.db.set_value(
				"Monthly Close",
				close.name,
				{"latest_check_run": run.name, "latest_fingerprint": before},
				update_modified=False,
			)
		txn.commit()

		from erpcore.erp_core.monthly_close.notifications import publish_close_update

		publish_close_update(close.name)

	except Exception as exc:
		txn.rollback("mc_run_evaluate")
		frappe.log_error(title=f"erpcore: monthly close check run {run_name} failed")
		if attempt < MAX_ATTEMPTS:
			frappe.db.set_value(
				CHECK_RUN_DOCTYPE,
				run_name,
				{"status": RUN_QUEUED, "error": str(exc)[:2000]},
				update_modified=False,
			)
			txn.commit()
			frappe.enqueue(
				"erpcore.erp_core.monthly_close.check_runner.execute_check_run",
				queue="long",
				timeout=3600,
				enqueue_after_commit=True,
				run_name=run_name,
			)
		else:
			_mark(run_name, RUN_FAILED, str(exc))
			txn.commit()


def approved_waivers(close) -> dict[str, set[str]]:
	"""Finding signatures covered by approved, unexpired waivers for the close's current revision."""
	from frappe.utils import getdate, nowdate

	waivers = frappe.get_all(
		EXCEPTION_DOCTYPE,
		filters={"monthly_close": close.name, "revision": close.revision, "status": REQUEST_APPROVED},
		fields=["check_id", "finding_signature", "expires_on"],
	)
	covered: dict[str, set[str]] = {}
	for waiver in waivers:
		if waiver.expires_on and getdate(waiver.expires_on) < getdate(nowdate()):
			continue
		covered.setdefault(waiver.check_id, set()).add(waiver.finding_signature)
	return covered


def blocking_results(run, close) -> list:
	"""Blockers not covered by a matching approved waiver, plus errors in mandatory checks (never waivable)."""
	covered = approved_waivers(close)
	blocking = []
	for row in run.results:
		if row.status == ERROR and row.severity == BLOCKER:
			blocking.append(row)
		elif row.status == BLOCKER and row.finding_signature not in covered.get(row.check_id, set()):
			blocking.append(row)
	return blocking
