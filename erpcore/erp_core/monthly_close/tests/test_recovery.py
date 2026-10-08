# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Workers that die outside normal exception handling, stale attempts and revoked requesters.

A dead worker is emulated by recording a pending stage and never running (or
only partly running) its job, then moving its timestamps past the lease. Under
tests, `frappe.enqueue(..., enqueue_after_commit=True)` never reaches RQ, so the
queue reports the job as gone, as it would after a crash.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from erpcore.erp_core.monthly_close import check_runner, closing, lifecycle, native_lock
from erpcore.erp_core.monthly_close.constants import APPROVED, CLOSED, CLOSING
from erpcore.erp_core.monthly_close.tests.utils import (
	MANAGER,
	PREPARER,
	MonthlyCloseTestCase,
	as_user,
	complete_tasks,
	drive_to_approved,
	month,
	new_close,
)
from erpcore.tests.utils import TEST_COMPANY


def age_pending(name: str, seconds: int = closing.PENDING_LEASE_SECONDS + 60):
	past = add_to_date(now_datetime(), seconds=-seconds)
	frappe.db.set_value(
		"Monthly Close",
		name,
		{"pending_requested_at": past, "pending_started_at": past},
		update_modified=False,
	)


def request(name: str) -> str:
	with as_user(MANAGER):
		closing.request_hard_close(name)
	return frappe.db.get_value("Monthly Close", name, "pending_token")


def lock_only(name: str, token: str):
	"""Run the lock stage, then die before sealing."""
	with patch.object(closing, "seal"):
		closing.execute_hard_close(name, token)


class TestRecovery(MonthlyCloseTestCase):
	def test_dead_lock_worker_is_recovered_and_fenced(self):
		name = drive_to_approved(3)
		token = request(name)
		close = frappe.get_doc("Monthly Close", name)
		self.assertTrue(close.pending_job_id)
		self.assertEqual(close.pending_attempt, 1)

		# Still within the lease: not dead, and a second request is refused.
		closing.recover_stalled_closes()
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "pending_action"), closing.PENDING_LOCK)
		with as_user(MANAGER):
			self.assertRaises(frappe.ValidationError, closing.request_hard_close, name)

		age_pending(name)
		closing.recover_stalled_closes()
		close.reload()
		self.assertEqual(close.state, APPROVED)
		self.assertFalse(close.pending_action)
		self.assertIn("stopped without finishing", close.last_error)

		# The old worker, should it ever resume, is fenced off by its token.
		closing.execute_hard_close(name, token)
		close.reload()
		self.assertEqual(close.state, APPROVED)
		self.assertFalse(close.accounting_period)

		# A new request runs normally.
		new_token = request(name)
		self.assertNotEqual(new_token, token)
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "pending_attempt"), 2)
		closing.execute_hard_close(name, new_token)
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "state"), CLOSED)

	def test_dead_seal_worker_keeps_lock_and_can_retry(self):
		name = drive_to_approved(3)
		token = request(name)
		lock_only(name, token)
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(close.state, CLOSING)
		self.assertEqual(close.pending_action, closing.PENDING_SEAL)
		self.assertFalse(close.last_error)

		# A seal that may still be running cannot be retried...
		with as_user(MANAGER):
			self.assertRaisesRegex(frappe.ValidationError, "still running", closing.retry_seal, name)

		# ...until its lease has passed and the queue no longer has it.
		age_pending(name)
		closing.recover_stalled_closes()
		close.reload()
		self.assertEqual(close.state, CLOSING)
		self.assertEqual(close.pending_action, closing.SEAL_FAILED)
		self.assertEqual(native_lock.lock_health(close)["status"], "Healthy")

		with as_user(MANAGER):
			closing.retry_seal(name)
		retry_token = frappe.db.get_value("Monthly Close", name, "pending_token")

		closing.seal(name, token)  # the dead worker's token: no effect
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "state"), CLOSING)
		closing.seal(name, retry_token)
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "state"), CLOSED)

	def test_revoked_or_disabled_after_enqueue(self):
		name = drive_to_approved(3)
		token = request(name)
		frappe.db.set_single_value("Monthly Close Settings", "enabled", 0)
		closing.execute_hard_close(name, token)
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(close.state, APPROVED)
		self.assertIn("disabled", close.last_error)
		self.assertFalse(close.accounting_period)
		frappe.db.set_single_value("Monthly Close Settings", "enabled", 1)

		# A requester who loses the role between request and execution.
		user = "_test_close_revoked@example.com"
		from erpcore.tests.utils import make_user

		make_user(user, "_Test", "revoked", roles=["Close Manager", "Accounts Manager"])
		with as_user(user):
			closing.request_hard_close(name)
		token = frappe.db.get_value("Monthly Close", name, "pending_token")
		frappe.get_doc("User", user).remove_roles("Close Manager")
		frappe.clear_cache(user=user)
		closing.execute_hard_close(name, token)
		close.reload()
		self.assertEqual(close.state, APPROVED)
		self.assertIn("Close Manager", close.last_error)

	def test_seal_refused_for_revoked_requester_keeps_lock(self):
		name = drive_to_approved(3)
		token = request(name)
		lock_only(name, token)
		frappe.db.set_single_value("Monthly Close Settings", "enabled", 0)
		closing.seal(name, token)
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(close.state, CLOSING)
		self.assertEqual(close.pending_action, closing.SEAL_FAILED)
		self.assertEqual(native_lock.lock_health(close)["status"], "Healthy")
		frappe.db.set_single_value("Monthly Close Settings", "enabled", 1)

	def test_duplicate_and_stale_check_workers(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
		complete_tasks(name)
		with as_user(PREPARER):
			run_name = lifecycle.request_check_run(name)

		claimed = check_runner.claim_run(run_name)
		self.assertTrue(claimed)
		first_token = claimed[0]

		# A duplicate job while the lease is live does nothing.
		check_runner.execute_check_run(run_name)
		run = frappe.get_doc("Monthly Close Check Run", run_name)
		self.assertEqual(run.status, "Running")
		self.assertEqual(run.claim_token, first_token)
		self.assertFalse(run.results)

		# Once the lease has expired, another worker takes over with a new token and finishes.
		frappe.db.set_value(
			"Monthly Close Check Run",
			run_name,
			"lease_expires_at",
			add_to_date(now_datetime(), seconds=-1),
			update_modified=False,
		)
		check_runner.execute_check_run(run_name)
		run.reload()
		self.assertEqual(run.status, "Completed")
		self.assertNotEqual(run.claim_token, first_token)
		self.assertTrue(run.results)
		self.assertTrue(run.snapshot_at)

	def test_check_run_with_changed_policy_is_stale(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
			run_name = lifecycle.request_check_run(name)
		policy = frappe.get_doc("Monthly Close Policy", TEST_COMPANY)
		policy.allow_self_approval = 1
		policy.cutover_period = month(36)
		policy.save(ignore_permissions=True)

		check_runner.execute_check_run(run_name)
		self.assertEqual(frappe.db.get_value("Monthly Close Check Run", run_name, "status"), "Stale")
