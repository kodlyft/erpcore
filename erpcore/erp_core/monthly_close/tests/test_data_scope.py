# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""The full-company evidence boundary for users with narrower User Permissions.

Documented rule (docs/monthly-closing/roles-and-data-scope.md): holding a close
role for a company grants that company's *full* financial evidence, whatever
Customer / Cost Center / Warehouse User Permissions the user has; without a
close role, a user gets none of it. These tests hold the rule to every path
evidence leaves through: background jobs, reports, list exports, packet export
and file downloads.
"""

import json

import frappe
from frappe.utils import add_days, get_last_day

from erpcore.erp_core.monthly_close import api, check_runner, closing, snapshot
from erpcore.erp_core.monthly_close.constants import ROLE_MANAGER, ROLE_PREPARER, ROLE_REVIEWER
from erpcore.erp_core.monthly_close.tests.test_permissions import restrict
from erpcore.erp_core.monthly_close.tests.test_posting_paths import party_je
from erpcore.erp_core.monthly_close.tests.utils import MonthlyCloseTestCase, as_user, drive_to_approved, month
from erpcore.tests.utils import TEST_COMPANY, make_user

NARROW = "_test_close_narrow_scope@example.com"
NO_CLOSE_ROLE = "_test_accounts_no_close_role@example.com"
HIDDEN_CUSTOMER = "_Test Customer"


def grant(user: str, allow: str, value: str):
	frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": allow,
			"for_value": value,
			"apply_to_all_doctypes": 1,
		}
	).insert(ignore_permissions=True)


def receivable_parties() -> set:
	from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import execute

	filters = frappe._dict(
		company=TEST_COMPANY,
		report_date=get_last_day(month(3)),
		ageing_based_on="Due Date",
		range="30, 60, 90, 120",
	)
	return {row.get("party") for row in execute(filters)[1] if isinstance(row, dict)}


class TestDataScope(MonthlyCloseTestCase):
	def setUp(self):
		super().setUp()
		make_user(
			NARROW, "_Test", "narrow", roles=[ROLE_MANAGER, ROLE_PREPARER, ROLE_REVIEWER, "Accounts Manager"]
		)
		make_user(NO_CLOSE_ROLE, "_Test", "no close role", roles=["Accounts Manager"])
		for user in (NARROW, NO_CLOSE_ROLE):
			restrict(user, TEST_COMPANY)
			grant(user, "Customer", "_Test Customer 1")
			grant(user, "Cost Center", "_Test Cost Center 2 - _TC")
			grant(user, "Warehouse", "_Test Warehouse 1 - _TC")
			frappe.clear_cache(user=user)

		# A receivable for a customer and cost center the narrow user cannot see.
		party_je(add_days(month(3), 3), 750)
		self.close = drive_to_approved(3)

	def test_restrictions_are_real_outside_the_close(self):
		with as_user(NARROW):
			self.assertFalse(frappe.has_permission("Customer", "read", HIDDEN_CUSTOMER))
			self.assertNotIn(HIDDEN_CUSTOMER, frappe.get_list("Customer", pluck="name"))
			self.assertNotIn("Main - _TC", frappe.get_list("Cost Center", pluck="name"))
			# ERPNext's own AR report, run directly, hides the customer from this user.
			self.assertNotIn(HIDDEN_CUSTOMER, receivable_parties())
		self.assertIn(HIDDEN_CUSTOMER, receivable_parties())

	def test_background_jobs_of_a_narrow_close_user_see_the_whole_company(self):
		with as_user(NARROW):
			closing.request_hard_close(self.close)
			token = frappe.db.get_value("Monthly Close", self.close, "pending_token")
			closing.execute_hard_close(self.close, token)
		self.assertEqual(frappe.db.get_value("Monthly Close", self.close, "state"), "Closed")

		ar = frappe.db.get_value(
			"Monthly Close Snapshot",
			{"monthly_close": self.close, "report_name": "Accounts Receivable Summary"},
			["name", "file"],
			as_dict=True,
		)
		with as_user(NARROW):
			content = frappe.get_doc("File", {"file_url": ar.file}).get_content()
		parties = {row.get("party") for row in json.loads(content)["rows"] if isinstance(row, dict)}
		self.assertIn(HIDDEN_CUSTOMER, parties, "the sealed evidence must not shrink to the user's customers")

	def test_evidence_paths_follow_the_close_role_grant(self):
		self.test_background_jobs_of_a_narrow_close_user_see_the_whole_company()
		snap = frappe.get_all("Monthly Close Snapshot", filters={"monthly_close": self.close}, pluck="name")[
			0
		]
		file_url = frappe.db.get_value("Monthly Close Snapshot", snap, "file")
		file_doc = frappe.get_doc("File", {"file_url": file_url})

		from erpcore.erp_core.report.monthly_close_status.monthly_close_status import execute as status_report

		with as_user(NARROW):
			# Downloads, list export (Report View uses get_list), packet export, close reports.
			self.assertTrue(frappe.has_permission("File", "read", doc=file_doc))
			self.assertIn(snap, frappe.get_list("Monthly Close Snapshot", pluck="name"))
			self.assertTrue(api.export_revision_packet(self.close, 1)["Close Packet"]["verified"])
			self.assertTrue(snapshot.verify(snap)["ok"])
			self.assertIn(self.close, [r.get("name") for r in status_report({})[1]])

		with as_user(NO_CLOSE_ROLE):
			# Same company access, no close role: no evidence by any path.
			self.assertFalse(frappe.has_permission("File", "read", doc=file_doc))
			self.assertFalse(frappe.has_permission("Monthly Close Snapshot", "read", snap))
			self.assertRaises(frappe.PermissionError, frappe.get_list, "Monthly Close Snapshot")
			self.assertRaises(frappe.PermissionError, api.export_revision_packet, self.close, 1)
			self.assertRaises(frappe.PermissionError, api.get_dashboard, self.close)
			# A job evaluated for this user would read the books as Administrator: refused first.
			self.assertRaises(
				frappe.PermissionError,
				check_runner.evaluate,
				frappe.get_doc("Monthly Close", self.close),
				frappe.get_doc("Monthly Close Policy", TEST_COMPANY),
			)
