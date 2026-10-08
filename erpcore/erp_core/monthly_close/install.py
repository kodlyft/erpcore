# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Idempotent installation for Monthly Closing, called from `erpcore.setup.after_migrate`.

* Roles are created only if missing.
* The default template and settings are seeded once. A site-level marker
  records that, so a template an administrator edited or deleted is not put
  back on the next migrate.
* No policy is created, so an upgrade never starts managing, closing or
  freezing any historical month.
* Unique keys are added only after checking for duplicates. If any exist,
  migration stops with remediation guidance; nothing is deleted.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Count

from erpcore.erp_core.monthly_close.constants import (
	CLOSE_ROLES,
	DEFAULT_TEMPLATE,
	OWNER_FIELD,
	ROLE_PREPARER,
	ROLE_REVIEWER,
)

SEED_MARKER = "erpcore_monthly_close_seeded"

DEFAULT_TASKS = (
	# key, title, category, mandatory, evidence, manual, depends_on, role, due offset, instructions
	(
		"drafts",
		"Resolve draft vouchers dated in the month",
		"Preparation",
		1,
		0,
		0,
		"",
		ROLE_PREPARER,
		2,
		"Submit, re-date or delete drafts, or request an exception for the Draft Vouchers check. Nothing is submitted automatically.",
	),
	(
		"depreciation",
		"Post depreciation and deferred revenue/expense due",
		"Preparation",
		1,
		0,
		0,
		"",
		ROLE_PREPARER,
		2,
		"Use ERPNext's asset depreciation and Process Deferred Accounting. The close checks only report what is still due.",
	),
	(
		"bank",
		"Reconcile and certify every bank account",
		"Reconciliation",
		1,
		0,
		0,
		"",
		ROLE_PREPARER,
		3,
		"Prepare Bank Workpapers from the close, attach statements, explain differences, then have a reviewer certify them.",
	),
	(
		"receivables",
		"Review receivables ageing, advances and unallocated receipts",
		"Reconciliation",
		1,
		0,
		1,
		"",
		ROLE_PREPARER,
		3,
		"Unpaid invoices are normal. Record what you reviewed and any follow-up.",
	),
	(
		"payables",
		"Review payables ageing, advances and unallocated payments",
		"Reconciliation",
		1,
		0,
		1,
		"",
		ROLE_PREPARER,
		3,
		"Record what you reviewed and any follow-up.",
	),
	(
		"accruals",
		"Record accruals and prepaid expense adjustments",
		"Accruals",
		1,
		1,
		1,
		"",
		ROLE_PREPARER,
		3,
		"Attach the accrual schedule. Journals are posted through normal ERPNext entries.",
	),
	(
		"stock",
		"Review stock valuation and reposting",
		"Reconciliation",
		0,
		0,
		1,
		"",
		ROLE_PREPARER,
		3,
		"For perpetual-inventory companies. Mark Not Applicable for service companies.",
	),
	(
		"fx",
		"Review foreign currency balances and revaluation",
		"Review",
		0,
		0,
		1,
		"",
		ROLE_PREPARER,
		3,
		"Decide whether an Exchange Rate Revaluation is required under your accounting policy.",
	),
	(
		"tax",
		"Review tax ledgers for the month",
		"Tax",
		1,
		1,
		1,
		"",
		ROLE_PREPARER,
		5,
		"Attach your tax workpaper. The system does not judge tax compliance.",
	),
	(
		"payroll",
		"Confirm payroll is posted",
		"Payroll",
		0,
		0,
		1,
		"",
		ROLE_PREPARER,
		3,
		"Applies whether payroll runs in HRMS or elsewhere. Mark Not Applicable if there is no payroll.",
	),
	(
		"intercompany",
		"Agree intercompany balances",
		"Intercompany",
		0,
		0,
		1,
		"",
		ROLE_PREPARER,
		5,
		"Agree reciprocal balances with the counterpart companies.",
	),
	(
		"management_review",
		"Management review of Profit and Loss and Balance Sheet",
		"Approval",
		1,
		0,
		1,
		"drafts, depreciation, bank, accruals, tax",
		ROLE_REVIEWER,
		6,
		"Review the month's results and balance sheet and record your conclusion.",
	),
)


def setup_roles():
	for role_name in CLOSE_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def custom_fields() -> dict:
	return {
		"Accounting Period": [
			{
				"fieldname": OWNER_FIELD,
				"fieldtype": "Link",
				"label": "Monthly Close",
				"options": "Monthly Close",
				"insert_after": "disabled",
				"read_only": 1,
				"no_copy": 1,
				"description": "Set when ERP Core's Monthly Close created this period. Such periods change only through the close (reopen/reclose).",
			}
		]
	}


UNIQUE_KEYS = (
	("Monthly Close", ("company", "period_start"), "monthly_close_company_period"),
	(
		"Monthly Close Bank Certification",
		("monthly_close", "revision", "bank_account"),
		"mc_bank_cert_unique",
	),
)

INDEXES = (
	("Monthly Close", ("company", "state")),
	("Monthly Close Task", ("monthly_close", "revision", "status")),
	("Monthly Close Check Run", ("monthly_close", "revision", "status")),
	("Monthly Close Exception", ("monthly_close", "revision", "status")),
	("Monthly Close Event", ("monthly_close", "event_time")),
	("Monthly Close Snapshot", ("monthly_close", "revision")),
	("Monthly Close Reopen Request", ("monthly_close", "status")),
)


def create_indexes():
	for doctype, fields, index_name in UNIQUE_KEYS:
		if not frappe.db.table_exists(doctype):
			continue
		table = frappe.qb.DocType(doctype)
		columns = [table[f] for f in fields]
		duplicates = (
			frappe.qb.from_(table)
			.select(*columns, Count("*").as_("n"))
			.groupby(*columns)
			.having(Count("*") > 1)
			.limit(5)
		).run(as_dict=True)
		if duplicates:
			frappe.throw(
				_(
					"Cannot add the unique key on {0} ({1}) because these rows are duplicated: {2}. Merge or remove the duplicates manually, then run migrate again. Nothing was deleted."
				).format(doctype, ", ".join(fields), frappe.as_json(duplicates)),
				title=_("Duplicate Monthly Close Records"),
			)
		frappe.db.add_unique(doctype, list(fields), index_name)

	for doctype, fields in INDEXES:
		if frappe.db.table_exists(doctype):
			frappe.db.add_index(doctype, list(fields))


def seed_defaults():
	"""Seed the default template and settings once per site."""
	if frappe.db.get_default(SEED_MARKER):
		return

	if not frappe.db.exists("Monthly Close Template", DEFAULT_TEMPLATE):
		template = frappe.get_doc(
			{
				"doctype": "Monthly Close Template",
				"template_name": DEFAULT_TEMPLATE,
				"description": "Works without HRMS or regional apps. Copy it to tailor tasks per company.",
				"tasks": [
					{
						"task_key": key,
						"title": title,
						"category": category,
						"is_mandatory": mandatory,
						"evidence_required": evidence,
						"manual_certification": manual,
						"depends_on": depends_on,
						"assigned_role": role,
						"due_offset_days": offset,
						"description": instructions,
					}
					for key, title, category, mandatory, evidence, manual, depends_on, role, offset, instructions in DEFAULT_TASKS
				],
			}
		)
		template.insert(ignore_permissions=True)

	settings = frappe.get_doc("Monthly Close Settings")
	if not settings.default_template:
		settings.default_template = DEFAULT_TEMPLATE
	if settings.max_sample_rows is None or settings.max_sample_rows == 0:
		settings.max_sample_rows = 20
	settings.flags.ignore_permissions = True
	settings.save()

	frappe.db.set_default(SEED_MARKER, "1")


def after_migrate():
	if not frappe.db.exists("DocType", "Monthly Close"):
		return

	setup_roles()
	create_indexes()
	seed_defaults()
