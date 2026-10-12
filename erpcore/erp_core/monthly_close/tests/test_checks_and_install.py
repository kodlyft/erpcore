# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, get_last_day

from erpcore.erp_core.monthly_close import lifecycle
from erpcore.erp_core.monthly_close.checks.registry import (
	FINDING,
	NOT_APPLICABLE,
	PASSED,
	CheckContext,
	get_check,
	resolve_status,
)
from erpcore.erp_core.monthly_close.constants import DEFAULT_TEMPLATE, OWNER_FIELD
from erpcore.erp_core.monthly_close.install import SEED_MARKER, after_migrate
from erpcore.erp_core.monthly_close.posting_guard import ClosedMonthError, guard_ledger_entry
from erpcore.erp_core.monthly_close.tests.utils import (
	PREPARER,
	MonthlyCloseTestCase,
	as_user,
	drive_to_closed,
	make_je,
	month,
)
from erpcore.tests.utils import OTHER_COMPANY, TEST_COMPANY


def context(company=TEST_COMPANY, offset=3, tolerance=0.0, policy=None):
	start = month(offset)
	return CheckContext(
		company=company,
		period_start=start,
		period_end=get_last_day(start),
		fiscal_year=frappe.db.get_value(
			"Fiscal Year", {"year_start_date": ["<=", start], "year_end_date": [">=", start]}
		),
		close_name="MC-TEST",
		revision=1,
		currency=frappe.get_cached_value("Company", company, "default_currency"),
		precision=2,
		severity="Warning",
		tolerance=tolerance,
		sample_limit=5,
		policy=policy or frappe.get_doc("Monthly Close Policy", company),
	)


class TestChecks(MonthlyCloseTestCase):
	def test_tolerance_is_separate_from_precision(self):
		from erpcore.erp_core.monthly_close.checks.registry import Finding

		small = Finding(FINDING, amount=4.0)
		self.assertEqual(resolve_status(small, "Blocker", 5.0, True), PASSED)
		self.assertEqual(resolve_status(small, "Blocker", 0.0, True), "Blocker")
		self.assertEqual(resolve_status(small, "Blocker", 5.0, False), "Blocker")
		evidence = Finding(FINDING, amount=0.0, tolerance_applies=False)
		self.assertEqual(resolve_status(evidence, "Warning", 100.0, True), "Warning")
		integrity = Finding(FINDING, amount=0.01, minimum_severity="Blocker")
		self.assertEqual(resolve_status(integrity, "Warning", 100.0, True), "Blocker")
		self.assertEqual(resolve_status(Finding(NOT_APPLICABLE), "Blocker", 0, True), NOT_APPLICABLE)

	def test_draft_vouchers_are_counted_not_submitted(self):
		je = make_je(add_days(month(3), 2), 120, submit=False)
		finding = get_check("draft_vouchers").function(context())
		self.assertEqual(finding.outcome, FINDING)
		self.assertGreaterEqual(finding.count, 1)
		self.assertIn(je.name, [s["name"] for s in finding.samples])
		self.assertEqual(frappe.db.get_value("Journal Entry", je.name, "docstatus"), 0)

	def test_trial_balance_passes_on_real_vouchers(self):
		make_je(add_days(month(3), 2), 333)
		self.assertEqual(get_check("trial_balance").function(context()).outcome, PASSED)

	def test_suspense_accounts_need_explicit_selection(self):
		policy = frappe.get_doc("Monthly Close Policy", TEST_COMPANY)
		self.assertEqual(
			get_check("suspense_accounts").function(context(policy=policy)).outcome, NOT_APPLICABLE
		)

		make_je(add_days(month(3), 2), 50)
		policy.append("suspense_accounts", {"account": "_Test Cash - _TC", "tolerance": 0})
		self.assertEqual(get_check("suspense_accounts").function(context(policy=policy)).outcome, FINDING)

		policy.suspense_accounts[0].tolerance = 10**12
		self.assertEqual(get_check("suspense_accounts").function(context(policy=policy)).outcome, PASSED)

	def test_stock_check_not_applicable_without_perpetual_inventory(self):
		frappe.db.set_value("Company", OTHER_COMPANY, "enable_perpetual_inventory", 0)
		frappe.clear_cache(doctype="Company")
		finding = get_check("stock_accounting").function(context(OTHER_COMPANY))
		self.assertEqual(finding.outcome, NOT_APPLICABLE)
		self.assertTrue(finding.message)

	def test_absent_data_is_not_applicable_not_passed(self):
		self.assertFalse(frappe.db.exists("Asset", {"company": OTHER_COMPANY, "docstatus": 1}))
		for check_id in ("depreciation_due", "deferred_due", "fx_revaluation"):
			finding = get_check(check_id).function(context(OTHER_COMPANY, offset=40))
			self.assertEqual(finding.outcome, NOT_APPLICABLE, check_id)
			self.assertTrue(finding.message, check_id)

	def test_cheque_check_feature_detects_receipt(self):
		finding = get_check("cheque_readiness").function(context(OTHER_COMPANY))
		self.assertEqual(finding.outcome, NOT_APPLICABLE)

	def test_bank_check_requires_certified_evidence(self):
		finding = get_check("bank_reconciliation").function(context())
		self.assertEqual(finding.outcome, FINDING)
		self.assertFalse(finding.tolerance_applies)


class TestInstall(MonthlyCloseTestCase):
	def test_repeat_migrate_preserves_customisations(self):
		template = frappe.get_doc("Monthly Close Template", DEFAULT_TEMPLATE)
		template.tasks[0].title = "Customised title"
		template.save(ignore_permissions=True)
		policy = frappe.get_doc("Monthly Close Policy", TEST_COMPANY)
		policy.reminder_days_before_due = 9
		policy.save(ignore_permissions=True)

		after_migrate()
		after_migrate()

		self.assertEqual(
			frappe.get_doc("Monthly Close Template", DEFAULT_TEMPLATE).tasks[0].title, "Customised title"
		)
		self.assertEqual(
			frappe.db.get_value("Monthly Close Policy", TEST_COMPANY, "reminder_days_before_due"), 9
		)

	def test_deleted_default_template_is_not_reseeded(self):
		self.assertTrue(frappe.db.get_default(SEED_MARKER))
		frappe.db.set_single_value("Monthly Close Settings", "default_template", None)
		frappe.delete_doc("Monthly Close Template", DEFAULT_TEMPLATE, force=True, ignore_permissions=True)
		after_migrate()
		self.assertFalse(frappe.db.exists("Monthly Close Template", DEFAULT_TEMPLATE))

	def test_migrate_creates_no_policy_or_close(self):
		policies = frappe.db.count("Monthly Close Policy")
		closes = frappe.db.count("Monthly Close")
		after_migrate()
		self.assertEqual(frappe.db.count("Monthly Close Policy"), policies)
		self.assertEqual(frappe.db.count("Monthly Close"), closes)

	def test_uninstall_blocked_while_locks_are_active(self):
		from erpcore.uninstall import before_uninstall

		name = drive_to_closed(3)
		self.assertRaises(frappe.ValidationError, before_uninstall)

		# Detaching hands the lock to native ERPNext, still enabled; uninstall may then proceed.
		lifecycle.detach_owned_lock(name)
		period = frappe.db.get_value("Monthly Close", name, "accounting_period")
		self.assertFalse(frappe.db.get_value("Accounting Period", period, OWNER_FIELD))
		self.assertFalse(frappe.db.get_value("Accounting Period", period, "disabled"))
		before_uninstall()

	def test_disabled_module_keeps_existing_locks(self):
		name = drive_to_closed(3)
		frappe.db.set_single_value("Monthly Close Settings", "enabled", 0)
		with as_user(PREPARER):
			self.assertRaises(frappe.ValidationError, lifecycle.create_close, TEST_COMPANY, month(2))

		ledger_row = frappe._dict(
			doctype="GL Entry",
			company=TEST_COMPANY,
			posting_date=add_days(month(3), 1),
			voucher_type="Journal Entry",
			voucher_no="X",
		)
		self.assertRaises(ClosedMonthError, guard_ledger_entry, ledger_row)
		self.assertTrue(name)
