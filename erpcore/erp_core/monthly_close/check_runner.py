# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Evaluate close checks into durable Check Runs.

Background runs (`enqueue_check_run`) are enqueued only after the requesting
transaction commits. The run row carries its immutable inputs: close revision,
policy version and the hash of the frozen policy payload. The worker uses its
own short transactions:

1. Claim: lock the run row (locking read), give up if it is finished, or if
   another worker holds an unexpired lease. Otherwise write a new claim token
   and lease, and commit. A duplicate or retried job therefore cannot run the
   same attempt twice, and a worker that died is taken over once its lease
   expires.
2. Evaluate: a new transaction whose first plain read opens one REPEATABLE
   READ view. Every check and the fingerprint read that same view, so the
   results and the fingerprint describe one consistent state of the books, as
   at `snapshot_at`. (Taking a "before" and "after" fingerprint inside one
   view, as an earlier version did, compares a snapshot with itself and proves
   nothing.) Commits made after the view opened are not in the results; they
   change the fingerprint, which makes the run stale the next time freshness is
   checked (submit, approve, hard close, dashboard).
3. Fence: lock the close row and the run row with locking reads, which return
   the latest committed rows, and write the results only if the claim token is
   still ours, the run is still Running, and the close still has the run's
   revision, policy version and policy hash. A stale or superseded worker
   writes nothing.

Failures are retried up to MAX_ATTEMPTS with the error recorded on the run.

`run_checks_sync` is the authoritative evaluation used inside the hard-close
job, under the close lock, in the caller's transaction.
"""

import hashlib
import json
import uuid
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime

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
LEASE_SECONDS = 3600


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


def finding_signature(
	check_id: str,
	check_version: int,
	status: str,
	severity: str,
	tolerance,
	count,
	amount,
	identities: list | None,
	precision: int,
) -> str:
	"""Identity of a finding, so a waiver applies only to exactly what was reviewed.

	Binds the check and its code version, the configured severity/tolerance, the
	totals at the company currency's precision and every row of the finding,
	sorted canonically. The display sample plays no part.
	"""
	rows = sorted(json.dumps(row, sort_keys=True, default=str) for row in (identities or []))
	payload = json.dumps(
		[
			check_id,
			cint(check_version),
			status,
			severity,
			f"{flt(tolerance, precision):.{precision}f}",
			cint(count),
			f"{flt(amount, precision):.{precision}f}",
			identities is not None,
			rows,
		],
		default=str,
	)
	return hashlib.sha256(payload.encode()).hexdigest()


def evaluate(close, policy) -> list[dict]:
	import erpnext

	from erpcore.erp_core.monthly_close.permissions import require_finance_evidence_access

	require_finance_evidence_access(close.company)

	currency = erpnext.get_company_currency(close.company)
	precision = cint(frappe.get_precision("GL Entry", "debit", currency=currency)) or 2
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
				identities = finding.complete_identities()
				waivable = finding.waivable and identities is not None
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				frappe.log_error(title=f"erpcore: monthly close check {check_id} failed for {close.name}")
				status, count, amount, samples, route = ERROR, 0, 0.0, [], None
				identities, waivable = [], False
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
					"finding_signature": finding_signature(
						check_id,
						definition.version,
						status,
						config.severity,
						config.tolerance,
						count,
						amount,
						identities,
						precision,
					),
					"waivable": cint(waivable),
					"identity_count": len(identities) if identities is not None else 0,
				}
			)

	return results


def apply_results(run, results, digest, components, snapshot_at=None):
	run.snapshot_at = snapshot_at or now_datetime()
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


def new_run(close, purpose: str):
	run = frappe.get_doc(
		{
			"doctype": CHECK_RUN_DOCTYPE,
			"monthly_close": close.name,
			"company": close.company,
			"revision": close.revision,
			"policy_version": close.policy_version,
			"policy_hash": close.policy_hash,
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
	run = new_run(close, purpose)
	run.status = RUN_RUNNING
	run.started_at = now_datetime()
	run.attempts = 1
	digest, components = fingerprint.compute(close)
	apply_results(run, evaluate(close, policy), digest, components)
	with service_write():
		run.save(ignore_permissions=True)
	return run


def enqueue_check_run(close) -> str:
	run = new_run(close, "Background")
	run.db_set("job_id", f"monthly-close-checks::{run.name}", update_modified=False)
	frappe.enqueue(
		"erpcore.erp_core.monthly_close.check_runner.execute_check_run",
		queue="long",
		timeout=LEASE_SECONDS,
		job_id=run.job_id,
		enqueue_after_commit=True,
		run_name=run.name,
	)
	return run.name


def is_stale(run, close) -> bool:
	return (
		cint(run.revision) != cint(close.revision)
		or cint(run.policy_version) != cint(close.policy_version)
		or (run.policy_hash or "") != (close.policy_hash or "")
	)


def mark_run(run_name: str, status: str, error: str | None = None):
	values = {"status": status, "finished_at": now_datetime(), "lease_expires_at": None}
	if error is not None:
		values["error"] = error[:2000]
	frappe.db.set_value(CHECK_RUN_DOCTYPE, run_name, values, update_modified=False)


def locked_run(run_name: str):
	"""Current committed run row (locking read), or None."""
	run = frappe.qb.DocType(CHECK_RUN_DOCTYPE)
	rows = (
		frappe.qb.from_(run)
		.select(
			run.name,
			run.status,
			run.monthly_close,
			run.revision,
			run.policy_version,
			run.policy_hash,
			run.attempts,
			run.claim_token,
			run.lease_expires_at,
		)
		.where(run.name == run_name)
		.for_update()
		.run(as_dict=True)
	)
	return rows[0] if rows else None


def claim_run(run_name: str):
	"""Step 1. Returns (claim token, attempt) or None when this worker must not run."""
	txn.begin("mc_run_claim")
	row = locked_run(run_name)
	if not row or row.status not in (RUN_QUEUED, RUN_RUNNING):
		txn.rollback("mc_run_claim")
		return None

	if (
		row.status == RUN_RUNNING
		and row.lease_expires_at
		and get_datetime(row.lease_expires_at) > now_datetime()
	):
		txn.rollback("mc_run_claim")
		return None

	close = frappe.get_doc("Monthly Close", row.monthly_close, for_update=True)
	if is_stale(row, close):
		mark_run(run_name, RUN_STALE, _("The close changed revision or policy before this run started."))
		txn.commit()
		return None

	token = uuid.uuid4().hex
	attempt = cint(row.attempts) + 1
	frappe.db.set_value(
		CHECK_RUN_DOCTYPE,
		run_name,
		{
			"status": RUN_RUNNING,
			"started_at": now_datetime(),
			"attempts": attempt,
			"claim_token": token,
			"lease_expires_at": add_to_date(now_datetime(), seconds=LEASE_SECONDS),
		},
		update_modified=False,
	)
	txn.commit()
	return token, attempt


def execute_check_run(run_name: str):
	"""Background worker entry point."""
	claimed = claim_run(run_name)
	if not claimed:
		return
	token, attempt = claimed

	txn.begin("mc_run_evaluate")
	try:
		snapshot_at = now_datetime()
		run = frappe.get_doc(CHECK_RUN_DOCTYPE, run_name)
		close = frappe.get_doc("Monthly Close", run.monthly_close)
		policy = frappe.get_doc("Monthly Close Policy", close.policy)

		from erpcore.erp_core.doctype.monthly_close_policy.monthly_close_policy import (
			frozen_policy,
			policy_hash,
		)

		if run.policy_hash and policy_hash(frozen_policy(policy)) != run.policy_hash:
			txn.rollback("mc_run_evaluate")
			mark_run(run_name, RUN_STALE, _("The close policy changed after these checks were requested."))
			txn.commit()
			return

		digest, components = fingerprint.compute(close)
		results = evaluate(close, policy)

		close = frappe.get_doc("Monthly Close", run.monthly_close, for_update=True)
		current = locked_run(run_name)
		if not current or current.status != RUN_RUNNING or current.claim_token != token:
			txn.rollback("mc_run_evaluate")
			return
		if is_stale(current, close):
			txn.rollback("mc_run_evaluate")
			mark_run(
				run_name, RUN_STALE, _("The close changed revision or policy while checks were running.")
			)
			txn.commit()
			return

		run = frappe.get_doc(CHECK_RUN_DOCTYPE, run_name)
		apply_results(run, results, digest, components, snapshot_at)
		run.lease_expires_at = None
		with service_write():
			run.save(ignore_permissions=True)
			frappe.db.set_value(
				"Monthly Close",
				close.name,
				{"latest_check_run": run.name, "latest_fingerprint": digest},
				update_modified=False,
			)
		txn.commit()

		from erpcore.erp_core.monthly_close.notifications import publish_close_update

		publish_close_update(close.name)

	except Exception as exc:
		txn.rollback("mc_run_evaluate")
		frappe.log_error(title=f"erpcore: monthly close check run {run_name} failed")
		txn.begin("mc_run_failed")
		current = locked_run(run_name)
		if not current or current.claim_token != token:
			txn.rollback("mc_run_failed")
			return
		if attempt < MAX_ATTEMPTS:
			frappe.db.set_value(
				CHECK_RUN_DOCTYPE,
				run_name,
				{
					"status": RUN_QUEUED,
					"error": str(exc)[:2000],
					"claim_token": None,
					"lease_expires_at": None,
				},
				update_modified=False,
			)
			txn.commit()
			frappe.enqueue(
				"erpcore.erp_core.monthly_close.check_runner.execute_check_run",
				queue="long",
				timeout=LEASE_SECONDS,
				enqueue_after_commit=True,
				run_name=run_name,
			)
		else:
			mark_run(run_name, RUN_FAILED, str(exc))
			txn.commit()


def approved_waivers(close) -> dict[str, set[str]]:
	"""Finding signatures covered by approved, unexpired waivers for the close's current revision.

	A waiver whose supporting evidence was deleted or altered no longer counts.
	"""
	from frappe.utils import getdate, nowdate

	from erpcore.erp_core.monthly_close.evidence import verify

	waivers = frappe.get_all(
		EXCEPTION_DOCTYPE,
		filters={"monthly_close": close.name, "revision": close.revision, "status": REQUEST_APPROVED},
		fields=["check_id", "finding_signature", "expires_on", "evidence", "evidence_hash"],
	)
	covered: dict[str, set[str]] = {}
	for waiver in waivers:
		if waiver.expires_on and getdate(waiver.expires_on) < getdate(nowdate()):
			continue
		if waiver.evidence and verify(waiver.evidence, waiver.evidence_hash):
			continue
		covered.setdefault(waiver.check_id, set()).add(waiver.finding_signature)
	return covered


def blocking_results(run, close) -> list:
	"""Blockers not covered by a matching approved waiver, plus errors in mandatory checks.

	Errors in mandatory checks and non-waivable findings (unbalanced ledgers,
	unfinished reposts) block whatever exceptions were approved.
	"""
	covered = approved_waivers(close)
	blocking = []
	for row in run.results:
		if row.status == ERROR and row.severity == BLOCKER:
			blocking.append(row)
		elif row.status == BLOCKER and (
			not cint(row.waivable) or row.finding_signature not in covered.get(row.check_id, set())
		):
			blocking.append(row)
	return blocking
