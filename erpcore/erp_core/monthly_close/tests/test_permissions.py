# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe

from erpcore.erp_core.monthly_close import api, lifecycle
from erpcore.erp_core.monthly_close.tests.utils import (
	AUDITOR,
	OTHER_COMPANY_USER,
	PREPARER,
	REVIEWER,
	MonthlyCloseTestCase,
	as_user,
	drive_to_closed,
	new_close,
)
from erpcore.tests.utils import TEST_COMPANY


class TestPermissions(MonthlyCloseTestCase):
	def test_other_company_user_cannot_reach_company_a_records(self):
		name = drive_to_closed(3)
		task = frappe.get_all("Monthly Close Task", filters={"monthly_close": name}, pluck="name")[0]
		event = frappe.get_all("Monthly Close Event", filters={"monthly_close": name}, pluck="name")[0]
		snap = frappe.get_all("Monthly Close Snapshot", filters={"monthly_close": name}, pluck="name")[0]
		run = frappe.db.get_value("Monthly Close", name, "latest_check_run")
		period = frappe.db.get_value("Monthly Close", name, "accounting_period")

		with as_user(OTHER_COMPANY_USER):
			for doctype, docname in (
				("Monthly Close", name),
				("Monthly Close Task", task),
				("Monthly Close Event", event),
				("Monthly Close Snapshot", snap),
				("Monthly Close Check Run", run),
				("Monthly Close Policy", TEST_COMPANY),
			):
				self.assertFalse(frappe.has_permission(doctype, "read", docname), doctype)
				self.assertNotIn(docname, frappe.get_list(doctype, pluck="name"), doctype)

			self.assertRaises(frappe.PermissionError, api.get_dashboard, name)
			self.assertRaises(frappe.PermissionError, api.get_packet, name)
			self.assertRaises(frappe.PermissionError, api.request_reopen, name, "not mine")
			self.assertRaises(frappe.PermissionError, api.verify_snapshot, snap)
			self.assertRaises(frappe.PermissionError, lifecycle.create_close, TEST_COMPANY, "2020-01-01")

			from erpcore.erp_core.report.monthly_close_audit_trail.monthly_close_audit_trail import execute

			_columns, rows = execute({})
			self.assertFalse([r for r in rows if r.get("monthly_close") == name])

			# Private snapshot files are served only to users who can read the snapshot.
			file_url = frappe.db.get_value("Monthly Close Snapshot", snap, "file")
			file_doc = frappe.get_doc("File", {"file_url": file_url})
			self.assertTrue(file_doc.is_private)
			self.assertFalse(file_doc.has_permission("read"))

		self.assertTrue(period)

	def test_roles_gate_actions(self):
		name = new_close(3)
		with as_user(AUDITOR):
			self.assertRaises(frappe.PermissionError, lifecycle.start_close, name)
			api.get_dashboard(name)  # read-only access works
		with as_user(REVIEWER):
			self.assertRaises(frappe.PermissionError, lifecycle.start_close, name)
		with as_user(PREPARER):
			lifecycle.start_close(name)
			self.assertRaises(frappe.PermissionError, lifecycle.approve, name)

	def test_users_cannot_write_service_records(self):
		name = new_close(3)
		with as_user(PREPARER):
			lifecycle.start_close(name)
			self.assertRaises(
				frappe.PermissionError,
				frappe.get_doc(
					{
						"doctype": "Monthly Close Exception",
						"monthly_close": name,
						"company": TEST_COMPANY,
						"status": "Approved",
					}
				).insert,
				ignore_permissions=True,
			)
			self.assertRaises(
				frappe.PermissionError,
				frappe.get_doc(
					{"doctype": "Monthly Close Check Run", "monthly_close": name, "status": "Completed"}
				).insert,
				ignore_permissions=True,
			)
