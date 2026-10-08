# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, getdate

from erpcore.erp_core.monthly_close import closing, lifecycle, native_lock, reopen, snapshot
from erpcore.erp_core.monthly_close.constants import (
	APPROVED,
	CLOSED,
	CLOSING,
	IN_PROGRESS,
	OWNER_FIELD,
	READY_FOR_REVIEW,
	REOPENED,
)
from erpcore.erp_core.monthly_close.posting_guard import ClosedMonthError, guard_ledger_entry
from erpcore.erp_core.monthly_close.tests.utils import (
	MANAGER,
	MANAGER_2,
	PREPARER,
	REVIEWER,
	MonthlyCloseTestCase,
	as_user,
	complete_tasks,
	drive_to_approved,
	drive_to_closed,
	gl_count,
	hard_close,
	make_je,
	month,
	reopen_close,
	run_checks,
)
from erpcore.tests.utils import TEST_COMPANY


class TestHardClose(MonthlyCloseTestCase):
	def test_full_close_locks_month_without_posting(self):
		start = month(3)
		make_je(add_days(start, 2), 250)
		before = gl_count()
		name = drive_to_approved(3)

		close = hard_close(name)

		self.assertEqual(close.state, CLOSED, close.last_error)
		self.assertTrue(close.lock_owned)
		self.assertEqual(gl_count(), before, "closing must not create GL entries")
		self.assertFalse(
			frappe.db.exists("Period Closing Voucher", {"company": TEST_COMPANY, "docstatus": 1})
		)

		period = frappe.get_doc("Accounting Period", close.accounting_period)
		self.assertEqual(period.get(OWNER_FIELD), name)
		self.assertFalse(period.disabled)
		self.assertFalse(period.exempted_role)
		self.assertEqual(getdate(period.start_date), getdate(close.period_start))
		self.assertEqual(native_lock.coverage_problems(period.name, close), [])
		self.assertEqual(native_lock.lock_health(close)["status"], "Healthy")

		snaps = frappe.get_all(
			"Monthly Close Snapshot",
			filters={"monthly_close": name},
			fields=["name", "report_name", "scope", "status"],
		)
		reports = {(s.report_name, s.scope) for s in snaps}
		self.assertIn(("Trial Balance", "Month"), reports)
		self.assertIn(("Profit and Loss Statement", "Year to Date"), reports)
		self.assertIn(("Balance Sheet", "As at Month End"), reports)
		for s in snaps:
			self.assertEqual(s.status, "Original")
			self.assertTrue(snapshot.verify(s.name)["ok"])

		events = frappe.get_all("Monthly Close Event", filters={"monthly_close": name}, pluck="event_type")
		for expected in (
			"Created",
			"Started",
			"Approved",
			"Hard Close Requested",
			"Lock Established",
			"Closed",
		):
			self.assertIn(expected, events)

	def test_closed_month_refuses_postings_but_next_month_works(self):
		start = month(3)
		je = make_je(add_days(start, 1), 75)
		name = drive_to_closed(3)

		self.assertRaises(frappe.ValidationError, make_je, add_days(start, 5), 10)

		je.reload()
		self.assertRaises(frappe.ValidationError, je.cancel)

		# The month after the closed one is still open for business.
		make_je(add_days(month(2), 3), 10)

		# The posting guard refuses on its own as well, even if the native period were not enforced.
		frappe.db.set_value(
			"Accounting Period",
			frappe.db.get_value("Monthly Close", name, "accounting_period"),
			"disabled",
			1,
		)
		ledger_row = frappe._dict(
			doctype="GL Entry",
			company=TEST_COMPANY,
			posting_date=add_days(start, 5),
			voucher_type="Journal Entry",
			voucher_no="X",
		)
		self.assertRaises(ClosedMonthError, guard_ledger_entry, ledger_row)

	def test_cannot_hard_close_before_month_end_or_out_of_sequence(self):
		current = month(0)
		self.assertRaises(frappe.ValidationError, drive_to_approved_and_close, 0)
		self.assertFalse(
			frappe.db.exists("Accounting Period", {"company": TEST_COMPANY, "start_date": current})
		)

	def test_sequence_after_cutover(self):
		# Cutover at month(4): month(3) cannot close until month(4) is closed.
		from erpcore.erp_core.monthly_close.tests.utils import set_cutover

		name = drive_to_approved(3, cutover=False)
		set_cutover(TEST_COMPANY, month(4))
		with as_user(MANAGER):
			self.assertRaises(closing.HardCloseRefused, closing.request_hard_close, name)

	def test_approval_is_invalidated_by_later_posting(self):
		name = drive_to_approved(3)
		make_je(add_days(month(3), 6), 40)
		close = hard_close(name)
		self.assertEqual(close.state, APPROVED)
		self.assertIn("changed after approval", close.last_error)
		self.assertFalse(close.pending_action)
		self.assertFalse(
			frappe.db.exists("Accounting Period", {"company": TEST_COMPANY, "start_date": month(3)})
		)

	def test_injected_failures_leave_no_partial_close(self):
		for stage in ("lock", "event", "state"):
			with self.subTest(stage=stage):
				name = drive_to_approved(3)
				frappe.flags.erpcore_close_fail_at = stage
				close = hard_close(name)
				frappe.flags.erpcore_close_fail_at = None

				self.assertEqual(close.state, APPROVED)
				self.assertIn("Injected failure", close.last_error)
				self.assertFalse(
					frappe.db.exists(
						"Accounting Period", {"company": TEST_COMPANY, "start_date": month(3), "disabled": 0}
					)
				)
				frappe.db.rollback()

	def test_seal_failure_keeps_lock_and_can_retry_or_abort(self):
		name = drive_to_approved(3)
		frappe.flags.erpcore_close_fail_at = "snapshot"
		close = hard_close(name)
		frappe.flags.erpcore_close_fail_at = None

		self.assertEqual(close.state, CLOSING)
		self.assertEqual(close.pending_action, closing.SEAL_FAILED)
		self.assertEqual(native_lock.lock_health(close)["status"], "Healthy")
		self.assertRaises(frappe.ValidationError, make_je, add_days(month(3), 4), 10)

		with as_user(MANAGER):
			closing.retry_seal(name)
			token = frappe.db.get_value("Monthly Close", name, "pending_token")
			closing.seal(name, token)
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "state"), CLOSED)

	def test_abort_releases_only_owned_lock(self):
		name = drive_to_approved(3)
		frappe.flags.erpcore_close_fail_at = "snapshot"
		close = hard_close(name)
		frappe.flags.erpcore_close_fail_at = None
		with as_user(MANAGER):
			self.assertRaises(frappe.ValidationError, closing.abort_close, name, "")
			closing.abort_close(name, "Report server down")
		close.reload()
		self.assertEqual(close.state, APPROVED)
		self.assertTrue(frappe.db.get_value("Accounting Period", close.accounting_period, "disabled"))

	def test_owned_period_cannot_be_tampered_with(self):
		name = drive_to_closed(3)
		period = frappe.get_doc(
			"Accounting Period", frappe.db.get_value("Monthly Close", name, "accounting_period")
		)

		period.disabled = 1
		self.assertRaises(frappe.PermissionError, period.save)

		period.reload()
		period.exempted_role = "Accounts Manager"
		self.assertRaises(frappe.PermissionError, period.save)

		period.reload()
		period.closed_documents = period.closed_documents[1:]
		self.assertRaises(frappe.PermissionError, period.save)

		self.assertRaises(frappe.PermissionError, frappe.delete_doc, "Accounting Period", period.name)

		# A change made behind the API is reported as a failed lock, not a healthy close.
		frappe.db.set_value("Accounting Period", period.name, "disabled", 1)
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(native_lock.lock_health(close)["status"], "Failed")

	def test_new_period_cannot_claim_ownership(self):
		period = frappe.get_doc(
			{
				"doctype": "Accounting Period",
				"period_name": "_Test forged",
				"company": TEST_COMPANY,
				"start_date": month(20),
				"end_date": frappe.utils.get_last_day(month(20)),
				"closed_documents": closed_rows(),
			}
		)
		period.insert(ignore_permissions=True)
		existing_close = drive_to_approved(3)
		period.set(OWNER_FIELD, existing_close)
		self.assertRaises(frappe.PermissionError, period.save)

	def test_existing_external_periods(self):
		start = month(3)
		end = frappe.utils.get_last_day(start)
		external = frappe.get_doc(
			{
				"doctype": "Accounting Period",
				"period_name": "_Test External",
				"company": TEST_COMPANY,
				"start_date": start,
				"end_date": end,
				"closed_documents": closed_rows(),
			}
		).insert(ignore_permissions=True)

		name = drive_to_approved(3)
		with as_user(MANAGER):
			# Compatible but not associated: refused, nothing adopted.
			self.assertRaises(closing.HardCloseRefused, closing.request_hard_close, name)

		lifecycle.associate_external_period(name, external.name)
		close = hard_close(name)
		self.assertEqual(close.state, CLOSED, close.last_error)
		self.assertFalse(close.lock_owned)
		self.assertEqual(close.accounting_period, external.name)
		self.assertFalse(frappe.db.get_value("Accounting Period", external.name, OWNER_FIELD))

		# Reopening never disables someone else's period.
		reopen_close(name)
		self.assertFalse(frappe.db.get_value("Accounting Period", external.name, "disabled"))

	def test_overlapping_external_period_is_a_conflict(self):
		start = month(3)
		frappe.get_doc(
			{
				"doctype": "Accounting Period",
				"period_name": "_Test Quarter",
				"company": TEST_COMPANY,
				"start_date": add_days(start, -31),
				"end_date": frappe.utils.get_last_day(start),
				"closed_documents": closed_rows(),
			}
		).insert(ignore_permissions=True)

		name = drive_to_approved(3)
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(native_lock.assess(close).kind, native_lock.CONFLICT)
		with as_user(MANAGER):
			self.assertRaises(closing.HardCloseRefused, closing.request_hard_close, name)
		self.assertRaises(
			frappe.ValidationError, lifecycle.associate_external_period, name, "_Test Quarter - _TC"
		)

	def test_company_freeze_date_is_untouched(self):
		frozen = add_days(month(12), 5)
		frappe.db.set_value("Company", TEST_COMPANY, "accounts_frozen_till_date", frozen)
		name = drive_to_closed(3)
		reopen_close(name)
		self.assertEqual(
			getdate(frappe.db.get_value("Company", TEST_COMPANY, "accounts_frozen_till_date")),
			getdate(frozen),
		)

	def test_reopen_request_alone_unlocks_nothing(self):
		name = drive_to_closed(3)
		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, reopen.request_reopen, name, "  ")
			request = reopen.request_reopen(name, "Missed accrual")
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(close.state, CLOSED)
		self.assertFalse(frappe.db.get_value("Accounting Period", close.accounting_period, "disabled"))

		# Reviewers cannot decide reopen requests; managers can.
		with as_user(REVIEWER):
			self.assertRaises(frappe.PermissionError, reopen.decide_reopen, request, True)

	def test_requester_cannot_approve_own_reopen(self):
		name = drive_to_closed(3)
		with as_user(MANAGER):
			request = reopen.request_reopen(name, "Missed accrual")
			self.assertRaises(frappe.PermissionError, reopen.decide_reopen, request, True)
		with as_user(MANAGER_2):
			reopen.decide_reopen(request, True)
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "state"), REOPENED)

	def test_reopen_and_reclose_creates_new_revision(self):
		name = drive_to_closed(3)
		first = frappe.get_doc("Monthly Close", name)
		original_snaps = frappe.get_all(
			"Monthly Close Snapshot", filters={"monthly_close": name, "revision": 1}, pluck="name"
		)

		reopen_close(name)
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(close.state, REOPENED)
		self.assertEqual(close.revision, 2)
		self.assertTrue(frappe.db.get_value("Accounting Period", first.accounting_period, "disabled"))
		for snap in original_snaps:
			self.assertEqual(frappe.db.get_value("Monthly Close Snapshot", snap, "status"), "Superseded")
			self.assertTrue(snapshot.verify(snap)["ok"])

		# Corrections go through normal postings while reopened.
		make_je(add_days(month(3), 9), 15)

		with as_user(PREPARER):
			lifecycle.resume_close(name)
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "state"), IN_PROGRESS)
		self.assertEqual(frappe.db.count("Monthly Close Task", {"monthly_close": name, "revision": 2}), 3)
		complete_tasks(name)
		run_checks(name)
		with as_user(PREPARER):
			lifecycle.submit_for_review(name)
		with as_user(REVIEWER):
			lifecycle.approve(name)
		close = hard_close(name)

		self.assertEqual(close.state, CLOSED, close.last_error)
		self.assertEqual(
			close.accounting_period, first.accounting_period, "reclose must reuse the owned period"
		)
		self.assertEqual(
			frappe.db.count("Accounting Period", {"company": TEST_COMPANY, "start_date": month(3)}), 1
		)
		self.assertEqual(
			frappe.db.count(
				"Monthly Close Snapshot", {"monthly_close": name, "revision": 2, "status": "Original"}
			),
			len(original_snaps),
		)

	def test_reopening_earlier_month_invalidates_later_months(self):
		earlier = drive_to_closed(4)
		later = drive_to_approved(3, cutover=False)
		reopen_close(earlier)
		self.assertEqual(frappe.db.get_value("Monthly Close", later, "state"), IN_PROGRESS)

	def test_later_closed_month_keeps_lock_and_needs_revalidation(self):
		earlier = drive_to_closed(4)
		later = drive_to_closed(3, cutover=False)
		reopen_close(earlier)

		close = frappe.get_doc("Monthly Close", later)
		self.assertEqual(close.state, CLOSED)
		self.assertTrue(close.revalidation_required)
		self.assertFalse(frappe.db.get_value("Accounting Period", close.accounting_period, "disabled"))

		# Nothing changed in the later month's balances, so it revalidates.
		with as_user(MANAGER):
			closing.confirm_revalidation(later)
		self.assertFalse(frappe.db.get_value("Monthly Close", later, "revalidation_required"))

	def test_review_freezes_checklist(self):
		name = drive_to_approved(3)
		task = frappe.get_last_doc("Monthly Close Task", filters={"monthly_close": name})
		task.notes = "late edit"
		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, task.save)
		self.assertIn(frappe.db.get_value("Monthly Close", name, "state"), (APPROVED, READY_FOR_REVIEW))


def closed_rows():
	return [{"document_type": dt, "closed": 1} for dt in native_lock.required_doctypes()]


def drive_to_approved_and_close(offset):
	name = drive_to_approved(offset)
	with as_user(MANAGER):
		closing.request_hard_close(name)
	return name
