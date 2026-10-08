# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Evidence files, task completion identity and assignment, bank workpapers."""

import frappe
from frappe.utils import add_days, get_last_day

from erpcore.erp_core.monthly_close import evidence, lifecycle
from erpcore.erp_core.monthly_close.tests.utils import (
	MANAGER,
	PREPARER,
	REVIEWER,
	MonthlyCloseTestCase,
	as_user,
	month,
	new_close,
	private_file,
)
from erpcore.tests.utils import TEST_BANK_ACCOUNT, TEST_COMPANY


def started_close(offset: int = 3) -> str:
	name = new_close(offset)
	with as_user(PREPARER):
		lifecycle.start_close(name)
	return name


def task_named(close: str, key: str):
	return frappe.get_doc(
		"Monthly Close Task",
		frappe.db.get_value("Monthly Close Task", {"monthly_close": close, "task_key": key}),
	)


def finish_prep(close: str):
	with as_user(PREPARER):
		task = task_named(close, "prep")
		task.status = "Done"
		task.save()


class TestEvidence(MonthlyCloseTestCase):
	def test_evidence_must_be_a_real_private_file_of_this_close(self):
		name = started_close()
		finish_prep(name)
		task = task_named(name, "evidence")
		task.status = "Done"

		with as_user(PREPARER):
			for url in (
				"/private/files/does-not-exist.txt",  # fabricated path
				private_file("public.txt", b"x", ("Monthly Close Task", task.name), is_private=0),
				private_file("stray.txt", b"stray"),  # attached to nothing
				private_file("other.txt", b"other", ("Company", TEST_COMPANY)),  # attached elsewhere
			):
				task.reload()
				task.status = "Done"
				task.evidence = url
				self.assertRaises(frappe.ValidationError, task.save)

			task.reload()
			task.status = "Done"
			task.evidence = private_file("ok.txt", b"accrual schedule", ("Monthly Close Task", task.name))
			task.save()

		self.assertEqual(task.evidence_hash, evidence.sha256_of(b"accrual schedule"))

	def test_certified_evidence_is_protected_and_rechecked(self):
		name = started_close()
		finish_prep(name)
		task = task_named(name, "evidence")
		with as_user(PREPARER):
			task.status = "Done"
			task.evidence = private_file("sched.txt", b"schedule v1", ("Monthly Close Task", task.name))
			task.save()

		file_name = frappe.db.get_value("File", {"file_url": task.evidence}, "name")
		file_doc = frappe.get_doc("File", file_name)
		with as_user(MANAGER):
			self.assertRaises(frappe.PermissionError, frappe.delete_doc, "File", file_name)
		file_doc.is_private = 0
		self.assertRaises(frappe.PermissionError, file_doc.save)
		# Refusals happen before Frappe touches the disk: the bytes are still there and intact.
		self.assertIsNone(evidence.verify(task.evidence, task.evidence_hash))
		self.assertEqual(frappe.db.get_value("Monthly Close Task", task.name, "evidence"), task.evidence)

		close = frappe.get_doc("Monthly Close", name)
		self.assertNotIn(task.name, [t.name for t in lifecycle.incomplete_mandatory_tasks(close)])

		# Bytes swapped behind the API: the task no longer counts as complete.
		with open(frappe.get_doc("File", file_name).get_full_path(), "wb") as handle:
			handle.write(b"schedule v2")
		self.assertIn(task.name, [t.name for t in lifecycle.incomplete_mandatory_tasks(close)])
		self.assertIn("changed", evidence.verify(task.evidence, task.evidence_hash))

		# File record removed behind the API.
		frappe.db.delete("File", {"file_url": task.evidence})  # every record Frappe made for it
		self.assertIn("deleted", evidence.verify(task.evidence, task.evidence_hash))

	def test_completion_identity_comes_from_the_server(self):
		name = started_close()
		with as_user(PREPARER):
			task = task_named(name, "prep")
			task.status = "Done"
			task.completed_by = REVIEWER
			task.completed_at = "2000-01-01 00:00:00"
			task.evidence_hash = "f" * 64
			task.save()
		task.reload()
		self.assertEqual(task.completed_by, PREPARER)
		self.assertNotEqual(str(task.completed_at), "2000-01-01 00:00:00")
		self.assertFalse(task.evidence_hash)

		# Later saves keep the original completion; a client cannot rewrite it.
		with as_user(PREPARER):
			task.notes = "more detail"
			task.completed_by = MANAGER
			task.save()
		self.assertEqual(frappe.db.get_value("Monthly Close Task", task.name, "completed_by"), PREPARER)

	def test_assigned_role_task_and_self_assignment(self):
		name = started_close()
		task = task_named(name, "optional")
		frappe.db.set_value("Monthly Close Task", task.name, "assigned_role", "Close Reviewer")

		with as_user(PREPARER):
			task.reload()
			task.status = "Done"
			self.assertRaises(frappe.PermissionError, task.save)

			task.reload()
			task.assigned_to = PREPARER  # assign to self, then complete
			task.status = "Done"
			self.assertRaises(frappe.PermissionError, task.save)

		with as_user(REVIEWER):
			task.reload()
			task.status = "Done"
			task.save()
		self.assertEqual(frappe.db.get_value("Monthly Close Task", task.name, "completed_by"), REVIEWER)

	def test_bank_statement_must_support_month_end(self):
		name = started_close()
		cert = frappe.get_doc(
			{
				"doctype": "Monthly Close Bank Certification",
				"monthly_close": name,
				"bank_account": TEST_BANK_ACCOUNT,
			}
		)
		with as_user(PREPARER):
			cert.insert()
			cert.statement_file = private_file(
				"statement.txt", b"bank statement", ("Monthly Close Bank Certification", cert.name)
			)
			cert.statement_date = add_days(get_last_day(month(3)), -3)
			cert.statement_balance = cert.ledger_balance
			cert.difference_explanation = "Timing"
			cert.save()
		self.assertTrue(cert.statement_hash)

		with as_user(REVIEWER):
			self.assertRaisesRegex(
				frappe.ValidationError, "Bridging", lifecycle.certify_bank_account, cert.name
			)
		with as_user(PREPARER):
			cert.reload()
			cert.bridging_note = "Three days of receipts rolled forward, listed in the statement."
			cert.save()
		with as_user(REVIEWER):
			lifecycle.certify_bank_account(cert.name)

		cert.reload()
		self.assertEqual(cert.status, "Certified")
		from erpcore.erp_core.doctype.monthly_close_bank_certification.monthly_close_bank_certification import (
			certified_content_hash,
		)

		self.assertEqual(cert.certified_hash, certified_content_hash(cert))
		# A change behind the API no longer matches what was certified.
		frappe.db.set_value("Monthly Close Bank Certification", cert.name, "statement_balance", 1)
		cert.reload()
		self.assertNotEqual(cert.certified_hash, certified_content_hash(cert))

		# Statements far from month end cannot be bridged at all.
		with as_user(PREPARER):
			other = frappe.get_doc(
				{
					"doctype": "Monthly Close Bank Certification",
					"monthly_close": name,
					"bank_account": frappe.db.get_value(
						"Bank Account",
						{"company": TEST_COMPANY, "name": ["!=", TEST_BANK_ACCOUNT], "is_company_account": 1},
					),
					"statement_date": add_days(get_last_day(month(3)), 60),
				}
			)
			self.assertRaises(frappe.ValidationError, other.insert)

	def test_workpaper_scope_cannot_be_moved(self):
		name = started_close()
		with as_user(PREPARER):
			cert = frappe.get_doc(
				{
					"doctype": "Monthly Close Bank Certification",
					"monthly_close": name,
					"bank_account": TEST_BANK_ACCOUNT,
				}
			).insert()
			cert.revision = 7
			self.assertRaises(frappe.PermissionError, cert.save)
