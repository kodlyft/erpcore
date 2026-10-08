# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_first_day, getdate

from erpcore.erp_core.monthly_close.permissions import require_company_access

VERSIONED_FIELDS = ("enabled", "cutover_period", "template", "allow_self_approval")


class MonthlyClosePolicy(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpcore.erp_core.doctype.monthly_close_policy_account.monthly_close_policy_account import (
			MonthlyClosePolicyAccount,
		)
		from erpcore.erp_core.doctype.monthly_close_policy_check.monthly_close_policy_check import (
			MonthlyClosePolicyCheck,
		)

		allow_self_approval: DF.Check
		checks: DF.Table[MonthlyClosePolicyCheck]
		company: DF.Link
		cutover_period: DF.Date
		enabled: DF.Check
		policy_version: DF.Int
		reminder_days_before_due: DF.Int
		suspense_accounts: DF.Table[MonthlyClosePolicyAccount]
		template: DF.Link
	# end: auto-generated types

	def validate(self):
		require_company_access(self.company, self.doctype)
		self.cutover_period = get_first_day(getdate(self.cutover_period))
		self.validate_cutover()
		self.seed_checks()
		self.validate_checks()
		self.validate_accounts()
		self.bump_version()

	def validate_cutover(self):
		earliest = frappe.get_all(
			"Monthly Close",
			filters={"company": self.company},
			pluck="period_start",
			order_by="period_start asc",
			limit=1,
		)
		earliest = earliest[0] if earliest else None
		if earliest and getdate(earliest) < getdate(self.cutover_period):
			frappe.throw(
				_("Closes already exist from {0}. The first managed month cannot be later than that.").format(
					earliest
				)
			)

	def seed_checks(self):
		if self.checks:
			return

		from erpcore.erp_core.monthly_close.checks.registry import get_checks

		for check_id, definition in sorted(get_checks().items()):
			self.append(
				"checks",
				{"check_id": check_id, "enabled": 1, "severity": definition.default_severity, "tolerance": 0},
			)

	def validate_checks(self):
		from erpcore.erp_core.monthly_close.checks.registry import get_checks

		known = get_checks()
		seen = set()
		for row in self.checks:
			if row.check_id not in known:
				frappe.throw(
					_("Row {0}: {1} is not a registered check. Registered: {2}").format(
						row.idx, frappe.bold(row.check_id), ", ".join(sorted(known))
					)
				)
			if row.check_id in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.check_id))
			seen.add(row.check_id)

	def validate_accounts(self):
		for row in self.suspense_accounts:
			company, is_group = frappe.get_cached_value("Account", row.account, ["company", "is_group"])
			if company != self.company:
				frappe.throw(_("Row {0}: account {1} belongs to {2}.").format(row.idx, row.account, company))
			if is_group:
				frappe.throw(_("Row {0}: pick a ledger account, not group {1}.").format(row.idx, row.account))

	def bump_version(self):
		"""The server owns the version. A client-sent value is ignored on insert, update and import."""
		before = None if self.is_new() else self.get_doc_before_save()
		if not before:
			self.policy_version = 1
			return

		self.policy_version = cint(before.policy_version)
		if policy_signature(before) != policy_signature(self):
			self.policy_version += 1


def policy_signature(doc) -> str:
	return json.dumps(
		{
			"fields": [str(doc.get(f) or "") for f in VERSIONED_FIELDS],
			"checks": [
				[r.check_id, cint(r.enabled), r.severity, float(r.tolerance or 0)] for r in doc.checks
			],
			"accounts": [[r.account, float(r.tolerance or 0)] for r in doc.suspense_accounts],
		},
		sort_keys=True,
	)


def frozen_policy(policy) -> dict:
	"""Everything a close revision was evaluated under: controls, checks with code versions, accounts."""
	from erpcore.erp_core.monthly_close.checks.registry import get_checks

	registered = get_checks()
	configured = {row.check_id: row for row in policy.checks}
	checks = []
	for check_id, definition in sorted(registered.items()):
		row = configured.get(check_id)
		checks.append(
			{
				"check_id": check_id,
				"check_version": definition.version,
				"enabled": cint(row.enabled) if row else 1,
				"severity": (row.severity if row and row.severity else definition.default_severity),
				"tolerance": float(row.tolerance or 0) if row else 0.0,
				"configured": bool(row),
			}
		)
	return {
		"company": policy.company,
		"policy_version": cint(policy.policy_version),
		"enabled": cint(policy.enabled),
		"cutover_period": str(policy.cutover_period or ""),
		"template": policy.template,
		"allow_self_approval": cint(policy.allow_self_approval),
		"checks": checks,
		"suspense_accounts": [
			{"account": r.account, "tolerance": float(r.tolerance or 0)} for r in policy.suspense_accounts
		],
	}


def policy_hash(payload: dict) -> str:
	return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
