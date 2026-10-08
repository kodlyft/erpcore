# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe

from erpcore.erp_core.monthly_close import api, lifecycle
from erpcore.erp_core.monthly_close.constants import ROLE_MANAGER, ROLE_PREPARER
from erpcore.erp_core.monthly_close.tests.utils import (
	AUDITOR,
	OTHER_COMPANY_USER,
	PREPARER,
	REVIEWER,
	MonthlyCloseTestCase,
	as_user,
	drive_to_closed,
	month,
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

			rows = execute({})[1]
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

	def test_restricted_system_manager_keeps_company_restriction(self):
		"""System Manager is a role, not a data-scope bypass: Company User Permissions still apply."""
		from erpcore.erp_core.monthly_close import permissions
		from erpcore.tests.utils import OTHER_COMPANY, make_user

		user = "_test_close_restricted_sm@example.com"
		make_user(user, "_Test", "restricted sm", roles=["System Manager", ROLE_MANAGER])
		restrict(user, OTHER_COMPANY)
		name = new_close(3)

		with as_user(user):
			self.assertFalse(permissions.has_company_access(TEST_COMPANY))
			self.assertFalse(frappe.has_permission("Monthly Close", "read", name))
			self.assertNotIn(name, frappe.get_list("Monthly Close", pluck="name"))
			self.assertRaises(frappe.PermissionError, lifecycle.start_close, name)
			self.assertRaises(frappe.PermissionError, api.get_dashboard, name)
			self.assertRaises(
				frappe.PermissionError, permissions.require_finance_evidence_access, TEST_COMPANY
			)

	def test_permission_scoped_to_monthly_close_applies_everywhere(self):
		"""A User Permission `applicable_for` Monthly Close restricts service code exactly like the hooks."""
		from erpcore.erp_core.monthly_close import permissions
		from erpcore.tests.utils import OTHER_COMPANY, make_user

		user = "_test_close_scoped@example.com"
		make_user(user, "_Test", "scoped", roles=[ROLE_PREPARER, ROLE_MANAGER])
		restrict(user, OTHER_COMPANY, applicable_for="Monthly Close")
		name = new_close(3)

		with as_user(user):
			self.assertFalse(frappe.has_permission("Monthly Close", "read", name))
			self.assertFalse(permissions.has_company_access(TEST_COMPANY))
			self.assertRaises(frappe.PermissionError, lifecycle.start_close, name)
			self.assertRaises(frappe.PermissionError, lifecycle.create_close, TEST_COMPANY, month(5))


def restrict(user: str, company: str, applicable_for: str | None = None):
	frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": "Company",
			"for_value": company,
			"apply_to_all_doctypes": 0 if applicable_for else 1,
			"applicable_for": applicable_for,
		}
	).insert(ignore_permissions=True)
	frappe.clear_cache(user=user)
