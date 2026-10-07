# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Fixtures for the monthly close tests.

Users, the test template and the policy are committed once (like ERPNext's own
bootstrap data); everything a test does afterwards is rolled back by
ERPNextTestSuite.tearDown. Background jobs are driven by calling the worker
functions directly; their step boundaries become savepoints under tests
(see monthly_close/txn.py).
"""

from contextlib import contextmanager

import frappe
from frappe.utils import add_months, get_first_day, get_last_day, nowdate

from erpcore.erp_core.monthly_close import check_runner, closing, lifecycle, reopen
from erpcore.erp_core.monthly_close.constants import (
	ROLE_AUDITOR,
	ROLE_MANAGER,
	ROLE_PREPARER,
	ROLE_REVIEWER,
)
from erpcore.tests.utils import OTHER_COMPANY, TEST_COMPANY, ERPCoreTestCase, _make_user

PREPARER = "_test_close_preparer@example.com"
REVIEWER = "_test_close_reviewer@example.com"
MANAGER = "_test_close_manager@example.com"
MANAGER_2 = "_test_close_manager2@example.com"
AUDITOR = "_test_close_auditor@example.com"
OTHER_COMPANY_USER = "_test_close_other_company@example.com"

TEMPLATE = "_Test Close Template"

# Checks that depend on site data the bootstrap does not control are warnings in tests,
# so the lifecycle is driven by the checks under test, not by incidental fixtures.
TEST_CHECKS = {
	"draft_vouchers": "Blocker",
	"trial_balance": "Blocker",
	"bank_reconciliation": "Warning",
	"ar_ap_review": "Warning",
	"stock_accounting": "Warning",
	"depreciation_due": "Warning",
	"deferred_due": "Warning",
	"fx_revaluation": "Warning",
	"suspense_accounts": "Warning",
	"cheque_readiness": "Warning",
}


def month(offset: int):
	"""First day of the month `offset` months before the current one."""
	return get_first_day(add_months(nowdate(), -offset))


def setup_monthly_close_fixtures():
	from erpcore.erp_core.monthly_close.install import after_migrate

	after_migrate()

	for user, roles in (
		(PREPARER, [ROLE_PREPARER, "Accounts User"]),
		(REVIEWER, [ROLE_REVIEWER, "Accounts User"]),
		(MANAGER, [ROLE_MANAGER, "Accounts Manager"]),
		(MANAGER_2, [ROLE_MANAGER, "Accounts Manager"]),
		(AUDITOR, [ROLE_AUDITOR]),
		(OTHER_COMPANY_USER, [ROLE_PREPARER, ROLE_REVIEWER, ROLE_MANAGER, ROLE_AUDITOR]),
	):
		_make_user(user, "_Test", user.split("@")[0], roles=roles)

	if not frappe.db.exists(
		"User Permission", {"user": OTHER_COMPANY_USER, "allow": "Company", "for_value": OTHER_COMPANY}
	):
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": OTHER_COMPANY_USER,
				"allow": "Company",
				"for_value": OTHER_COMPANY,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Monthly Close Template", TEMPLATE):
		frappe.get_doc(
			{
				"doctype": "Monthly Close Template",
				"template_name": TEMPLATE,
				"tasks": [
					{"task_key": "prep", "title": "Prepare", "is_mandatory": 1, "due_offset_days": 2},
					{
						"task_key": "evidence",
						"title": "Attach evidence",
						"is_mandatory": 1,
						"evidence_required": 1,
						"depends_on": "prep",
						"due_offset_days": 3,
					},
					{"task_key": "optional", "title": "Optional review", "is_mandatory": 0},
				],
			}
		).insert(ignore_permissions=True)

	for company in (TEST_COMPANY, OTHER_COMPANY):
		ensure_policy(company)

	frappe.db.commit()  # nosemgrep


def ensure_policy(company: str):
	checks = [{"check_id": k, "enabled": 1, "severity": v, "tolerance": 0} for k, v in TEST_CHECKS.items()]
	if frappe.db.exists("Monthly Close Policy", company):
		policy = frappe.get_doc("Monthly Close Policy", company)
	else:
		policy = frappe.new_doc("Monthly Close Policy")
		policy.company = company

	policy.update(
		{
			"enabled": 1,
			"cutover_period": month(36),
			"template": TEMPLATE,
			"allow_self_approval": 0,
		}
	)
	policy.set("checks", checks)
	policy.set("suspense_accounts", [])
	policy.save(ignore_permissions=True)
	return policy


def set_cutover(company: str, start):
	"""Make `start` the first managed month, so sequence rules do not need earlier closes."""
	frappe.db.set_value("Monthly Close Policy", company, "cutover_period", start, update_modified=False)
	frappe.clear_document_cache("Monthly Close Policy", company)


@contextmanager
def as_user(user: str):
	previous = frappe.session.user
	frappe.set_user(user)  # nosemgrep
	try:
		yield
	finally:
		frappe.set_user(previous)  # nosemgrep


def private_file(name: str = "evidence.txt", content: bytes = b"evidence") -> str:
	doc = frappe.get_doc({"doctype": "File", "file_name": name, "is_private": 1, "content": content}).insert(
		ignore_permissions=True
	)
	return doc.file_url


def complete_tasks(close_name: str):
	close = frappe.get_doc("Monthly Close", close_name)
	tasks = frappe.get_all(
		"Monthly Close Task",
		filters={"monthly_close": close_name, "revision": close.revision},
		fields=["name", "evidence_required", "is_mandatory"],
		order_by="idx asc",
	)
	with as_user(PREPARER):
		for row in tasks:
			task = frappe.get_doc("Monthly Close Task", row.name)
			if not row.is_mandatory:
				task.status = "Not Applicable"
			else:
				task.status = "Done"
				if row.evidence_required:
					task.evidence = private_file()
			task.save()


def run_checks(close_name: str) -> str:
	with as_user(PREPARER):
		run = lifecycle.request_check_run(close_name)
	check_runner.execute_check_run(run)
	return run


def new_close(offset: int = 3, company: str = TEST_COMPANY, cutover: bool = True) -> str:
	start = month(offset)
	if cutover:
		set_cutover(company, start)
	with as_user(PREPARER):
		return lifecycle.create_close(company, start)


def drive_to_approved(offset: int = 3, company: str = TEST_COMPANY, cutover: bool = True) -> str:
	name = new_close(offset, company, cutover)
	with as_user(PREPARER):
		lifecycle.start_close(name)
	complete_tasks(name)
	run_checks(name)
	with as_user(PREPARER):
		lifecycle.submit_for_review(name)
	with as_user(REVIEWER):
		lifecycle.approve(name)
	return name


def hard_close(name: str):
	with as_user(MANAGER):
		closing.request_hard_close(name)
		token = frappe.db.get_value("Monthly Close", name, "pending_token")
		closing.execute_hard_close(name, token)
	return frappe.get_doc("Monthly Close", name)


def drive_to_closed(offset: int = 3, company: str = TEST_COMPANY, cutover: bool = True) -> str:
	name = drive_to_approved(offset, company, cutover)
	hard_close(name)
	return name


def reopen_close(name: str, reason: str = "Late supplier invoice"):
	with as_user(PREPARER):
		request = reopen.request_reopen(name, reason)
	with as_user(MANAGER):
		reopen.decide_reopen(request, True, "ok")
	return request


def make_je(posting_date, amount: float = 100, submit: bool = True, company: str = TEST_COMPANY):
	from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry

	return make_journal_entry(
		"_Test Bank - _TC",
		"_Test Cash - _TC",
		amount,
		posting_date=posting_date,
		save=True,
		submit=submit,
	)


def gl_count(company: str = TEST_COMPANY) -> int:
	return frappe.db.count("GL Entry", {"company": company})


class MonthlyCloseTestCase(ERPCoreTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_monthly_close_fixtures()

	def setUp(self):
		frappe.set_user("Administrator")  # nosemgrep
		frappe.flags.erpcore_close_fail_at = None

	def tearDown(self):
		frappe.set_user("Administrator")  # nosemgrep
		frappe.flags.erpcore_close_fail_at = None
		super().tearDown()

	@staticmethod
	def month_end(offset: int):
		return get_last_day(month(offset))
