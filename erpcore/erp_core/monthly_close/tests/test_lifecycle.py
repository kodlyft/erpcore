# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.utils import add_months, get_first_day, nowdate

from erpcore.erp_core.monthly_close import check_runner, lifecycle
from erpcore.erp_core.monthly_close.constants import (
	APPROVED,
	BLOCKER,
	DRAFT,
	IN_PROGRESS,
	READY_FOR_REVIEW,
)
from erpcore.erp_core.monthly_close.tests.utils import (
	MANAGER,
	PREPARER,
	REVIEWER,
	TEMPLATE,
	MonthlyCloseTestCase,
	as_user,
	complete_tasks,
	drive_to_approved,
	make_je,
	month,
	new_close,
	run_checks,
	set_cutover,
)
from erpcore.tests.utils import TEST_COMPANY


class TestLifecycle(MonthlyCloseTestCase):
	def test_one_close_per_company_and_month(self):
		name = new_close(4)
		self.assertTrue(name.endswith(month(4).strftime("%Y-%m")))
		with as_user(PREPARER):
			self.assertRaises(frappe.DuplicateEntryError, lifecycle.create_close, TEST_COMPANY, month(4))

	def test_any_date_in_month_is_normalised(self):
		start = month(5)
		set_cutover(TEST_COMPANY, start)
		with as_user(PREPARER):
			name = lifecycle.create_close(TEST_COMPANY, frappe.utils.add_days(start, 9))
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(str(close.period_start), str(start))
		self.assertEqual(close.state, DRAFT)
		self.assertEqual(close.revision, 1)

	def test_cutover_is_enforced(self):
		set_cutover(TEST_COMPANY, month(3))
		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, lifecycle.create_close, TEST_COMPANY, month(4))

	def test_future_month_can_be_planned(self):
		future = get_first_day(add_months(nowdate(), 2))
		set_cutover(TEST_COMPANY, month(1))
		with as_user(PREPARER):
			name = lifecycle.create_close(TEST_COMPANY, future)
			lifecycle.start_close(name)
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "state"), IN_PROGRESS)

	def test_direct_writes_to_controlled_fields_are_refused(self):
		name = new_close(3)
		doc = frappe.get_doc("Monthly Close", name)
		doc.state = "Closed"
		self.assertRaises(frappe.PermissionError, doc.save)

		doc.reload()
		doc.approved_fingerprint = "forged"
		self.assertRaises(frappe.PermissionError, doc.save)

		doc.reload()
		doc.notes = "allowed"
		doc.save()

	def test_insert_cannot_smuggle_state(self):
		start = month(6)
		set_cutover(TEST_COMPANY, start)
		with as_user(PREPARER):
			doc = frappe.get_doc(
				{
					"doctype": "Monthly Close",
					"company": TEST_COMPANY,
					"period_start": start,
					"state": "Closed",
					"approved_revision": 1,
					"approved_fingerprint": "forged",
				}
			).insert()
		self.assertEqual(doc.state, DRAFT)
		self.assertFalse(doc.approved_fingerprint)

	def test_start_freezes_template(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)

		template = frappe.get_doc("Monthly Close Template", TEMPLATE)
		template.append("tasks", {"task_key": "later", "title": "Added later", "is_mandatory": 1})
		template.save(ignore_permissions=True)

		close = frappe.get_doc("Monthly Close", name)
		keys = [t["task_key"] for t in json.loads(close.template_snapshot)["tasks"]]
		self.assertNotIn("later", keys)
		self.assertEqual(frappe.db.count("Monthly Close Task", {"monthly_close": name}), 3)

	def test_task_rules(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
			evidence_task = frappe.get_doc(
				"Monthly Close Task", {"monthly_close": name, "task_key": "evidence"}
			)
			evidence_task.status = "Done"
			# depends on "prep", which is still open, and needs evidence
			self.assertRaises(frappe.ValidationError, evidence_task.save)

			evidence_task.reload()
			evidence_task.title = "Renamed"
			self.assertRaises(frappe.PermissionError, evidence_task.save)

			evidence_task.reload()
			evidence_task.status = "Not Applicable"
			self.assertRaises(frappe.ValidationError, evidence_task.save)

			evidence_task.reload()
			evidence_task.evidence = "/files/public-evidence.txt"
			self.assertRaises(frappe.ValidationError, evidence_task.save)

	def test_review_requires_tasks_and_fresh_checks(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
			self.assertRaises(frappe.ValidationError, lifecycle.submit_for_review, name)

		complete_tasks(name)
		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, lifecycle.submit_for_review, name)

		run_checks(name)
		# A posting into the month after the checks makes them stale.
		make_je(frappe.utils.add_days(month(3), 3))
		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, lifecycle.submit_for_review, name)

		run_checks(name)
		with as_user(PREPARER):
			lifecycle.submit_for_review(name)
		self.assertEqual(frappe.db.get_value("Monthly Close", name, "state"), READY_FOR_REVIEW)

	def test_check_run_records_every_registered_check(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
		run = frappe.get_doc("Monthly Close Check Run", run_checks(name))
		self.assertEqual(run.status, "Completed", run.error)
		ids = {row.check_id for row in run.results}
		self.assertEqual(ids, set(check_runner.get_checks()))
		errors = [(r.check_id, r.message) for r in run.results if r.status == "Error"]
		self.assertFalse(errors, errors)
		trial_balance = next(r for r in run.results if r.check_id == "trial_balance")
		self.assertEqual(trial_balance.status, "Passed")

	def test_separation_of_duties(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
		complete_tasks(name)
		run_checks(name)
		with as_user(PREPARER):
			lifecycle.submit_for_review(name)

		# Manager holding reviewer rights can approve, but not someone who prepared the close.
		frappe.db.set_value("Monthly Close", name, "preparer", MANAGER)
		with as_user(MANAGER):
			self.assertRaises(frappe.PermissionError, lifecycle.approve, name)

		with as_user(REVIEWER):
			lifecycle.approve(name)
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(close.state, APPROVED)
		self.assertEqual(close.approved_revision, 1)
		self.assertEqual(close.approved_check_run, close.submitted_check_run)
		self.assertFalse(close.self_approval_used)

	def test_self_approval_only_with_policy_and_recorded(self):
		name = new_close(3)
		with as_user(MANAGER):
			lifecycle.start_close(name)
		complete_tasks(name)
		run_checks(name)
		with as_user(MANAGER):
			lifecycle.submit_for_review(name)
			self.assertRaises(frappe.PermissionError, lifecycle.approve, name)

		frappe.db.set_value("Monthly Close Policy", TEST_COMPANY, "allow_self_approval", 1)
		frappe.clear_document_cache("Monthly Close Policy", TEST_COMPANY)
		with as_user(MANAGER):
			lifecycle.approve(name)
		self.assertTrue(frappe.db.get_value("Monthly Close", name, "self_approval_used"))

	def test_send_back_requires_reason(self):
		name = drive_to_approved(3)
		with as_user(REVIEWER):
			self.assertRaises(frappe.ValidationError, lifecycle.reject, name, " ")
			lifecycle.reject(name, "Accrual schedule missing")
		close = frappe.get_doc("Monthly Close", name)
		self.assertEqual(close.state, IN_PROGRESS)
		self.assertEqual(close.rejection_reason, "Accrual schedule missing")
		self.assertFalse(close.approved_check_run)

	def test_blocker_needs_waiver_and_waiver_keeps_finding(self):
		start = month(3)
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
		complete_tasks(name)
		make_je(frappe.utils.add_days(start, 4), submit=False)  # a draft dated in the month
		run = frappe.get_doc("Monthly Close Check Run", run_checks(name))
		draft = next(r for r in run.results if r.check_id == "draft_vouchers")
		self.assertEqual(draft.status, BLOCKER)

		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, lifecycle.submit_for_review, name)
			waiver = lifecycle.request_waiver(name, draft.name, "Recurring template, reviewed")
			# The requester cannot approve their own exception.
			self.assertRaises(frappe.PermissionError, lifecycle.decide_waiver, waiver, True)

		with as_user(REVIEWER):
			lifecycle.decide_waiver(waiver, True, "fine")

		with as_user(PREPARER):
			lifecycle.submit_for_review(name)

		run.reload()
		self.assertEqual(next(r for r in run.results if r.check_id == "draft_vouchers").status, BLOCKER)
		self.assertEqual(frappe.db.get_value("Monthly Close Exception", waiver, "status"), "Approved")

	def test_mandatory_check_error_blocks(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
		complete_tasks(name)

		from erpcore.erp_core.monthly_close.checks import registry

		original = registry._REGISTRY["trial_balance"].function

		def broken(ctx):
			raise RuntimeError("boom")

		registry._REGISTRY["trial_balance"].function = broken
		try:
			run = frappe.get_doc("Monthly Close Check Run", run_checks(name))
		finally:
			registry._REGISTRY["trial_balance"].function = original

		row = next(r for r in run.results if r.check_id == "trial_balance")
		self.assertEqual(row.status, "Error")
		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, lifecycle.submit_for_review, name)
			self.assertRaises(frappe.ValidationError, lifecycle.request_waiver, name, row.name, "please")

	def test_stale_worker_cannot_overwrite_newer_revision(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
			run_name = lifecycle.request_check_run(name)
		frappe.db.set_value("Monthly Close", name, "revision", 2)
		check_runner.execute_check_run(run_name)
		self.assertEqual(frappe.db.get_value("Monthly Close Check Run", run_name, "status"), "Stale")
		self.assertFalse(frappe.db.get_value("Monthly Close", name, "latest_check_run"))

	def test_policy_change_invalidates_runs(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
		complete_tasks(name)
		run_checks(name)

		policy = frappe.get_doc("Monthly Close Policy", TEST_COMPANY)
		policy.cutover_period = month(36)  # valid whatever other closes exist on the site
		policy.allow_self_approval = 1
		policy.save(ignore_permissions=True)

		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, lifecycle.submit_for_review, name)

	def test_draft_close_can_be_deleted_started_cannot(self):
		name = new_close(3)
		frappe.delete_doc("Monthly Close", name)
		self.assertFalse(frappe.db.exists("Monthly Close", name))

		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, "Monthly Close", name)

	def test_events_are_append_only(self):
		name = new_close(3)
		event = frappe.get_last_doc("Monthly Close Event", filters={"monthly_close": name})
		event.event_type = "Tampered"
		self.assertRaises(frappe.PermissionError, event.save)
		self.assertRaises(frappe.PermissionError, frappe.delete_doc, "Monthly Close Event", event.name)
