# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Company scoping and role checks for every monthly close record.

The data-scope model (docs/monthly-closing/roles-and-data-scope.md):

* Company User Permissions restrict every user, System Managers included. Only
  `Administrator` is unrestricted; it is the documented administrative
  boundary. A System Manager without Company User Permissions sees every
  company, as everywhere else in Frappe.
* A permission row applies to the monthly close doctypes when it applies to all
  doctypes, or names the doctype in `applicable_for`. Service code checks the
  same rule as the permission hooks, always against a concrete doctype
  (Monthly Close by default), so both give the same answer.
* Holding a close role for a company is an explicit grant to review that
  company's *whole* financial evidence: checks and snapshots run as
  Administrator after `require_finance_evidence_access`, so narrower
  Customer/Cost Center/Warehouse User Permissions do not shrink the evidence.
  Users who must not see full-company figures must not hold close roles.

A user restricted to Company A never sees, exports or acts on Company B's
close, tasks, findings, evidence, events or lock records, whichever API they
come through.
"""

import frappe
from frappe import _

from erpcore.erp_core.monthly_close.constants import (
	CLOSE_DOCTYPE,
	CLOSE_ROLES,
	ROLE_AUDITOR,
	ROLE_MANAGER,
	ROLE_PREPARER,
	ROLE_REVIEWER,
)


def allowed_companies(user: str | None = None, doctype: str = CLOSE_DOCTYPE) -> list[str] | None:
	"""Companies the user may see in `doctype`, or None when they are not restricted."""
	user = user or frappe.session.user
	if user == "Administrator":
		return None

	rows = frappe.defaults.get_user_permissions(user).get("Company") or []
	companies = [
		row.get("doc")
		for row in rows
		if row.get("doc") and (not row.get("applicable_for") or row.get("applicable_for") == doctype)
	]
	return companies or None


def has_company_access(company: str, user: str | None = None, doctype: str = CLOSE_DOCTYPE) -> bool:
	companies = allowed_companies(user, doctype)
	return companies is None or company in companies


def query_conditions(doctype: str, user: str | None = None) -> str | None:
	companies = allowed_companies(user, doctype)
	if companies is None:
		return None

	allowed = ", ".join(frappe.db.escape(company) for company in companies)
	return f"`tab{doctype}`.`company` in ({allowed})"


def has_permission(doc, ptype=None, user=None, debug=False) -> bool:
	"""Doc-level hook shared by every company-scoped monthly close doctype.

	Frappe v16 treats any falsy return as a denial, so "no objection" is True.
	The hook can only narrow access; role permissions still decide the rest.
	"""
	company = doc.get("company")
	if not company:
		return True

	return has_company_access(company, user, doc.doctype)


def require_company_access(company: str, doctype: str = CLOSE_DOCTYPE) -> None:
	if not has_company_access(company, doctype=doctype):
		frappe.throw(
			_("You do not have access to company {0}.").format(frappe.bold(company)), frappe.PermissionError
		)


def require_role(*roles: str) -> None:
	user_roles = set(frappe.get_roles())
	if frappe.session.user == "Administrator" or user_roles & set(roles) or "System Manager" in user_roles:
		return

	frappe.throw(
		_("This action needs one of these roles: {0}.").format(", ".join(_(role) for role in roles)),
		frappe.PermissionError,
	)


def require_finance_evidence_access(company: str) -> None:
	"""Explicit grant before checks or snapshots read the company's books as Administrator."""
	user = frappe.session.user
	if user == "Administrator":
		return
	roles = set(frappe.get_roles(user))
	if not (roles & set(CLOSE_ROLES) or "System Manager" in roles):
		frappe.throw(_("Reading full-company close evidence needs a close role."), frappe.PermissionError)
	require_company_access(company)


def require_preparer():
	require_role(ROLE_PREPARER, ROLE_MANAGER)


def require_reviewer():
	require_role(ROLE_REVIEWER, ROLE_MANAGER)


def require_manager():
	require_role(ROLE_MANAGER)


def can_read_close_records():
	require_role(ROLE_PREPARER, ROLE_REVIEWER, ROLE_MANAGER, ROLE_AUDITOR)


def require_different_actor(actor: str | None, allow_self: bool, what: str) -> bool:
	"""Separation of duties. Returns True when the self-approval policy was relied on."""
	if not actor or actor != frappe.session.user:
		return False

	if allow_self:
		return True

	frappe.throw(
		_(
			"You cannot approve {0} that you prepared or requested yourself. Ask another authorised user."
		).format(what),
		frappe.PermissionError,
		title=_("Separation of Duties"),
	)


# permission_query_conditions hooks, one per doctype because Frappe passes only `user`.
def conditions_for(doctype):
	def conditions(user=None, doctype_=doctype):
		return query_conditions(doctype_, user)

	return conditions


monthly_close_conditions = conditions_for("Monthly Close")
policy_conditions = conditions_for("Monthly Close Policy")
task_conditions = conditions_for("Monthly Close Task")
check_run_conditions = conditions_for("Monthly Close Check Run")
exception_conditions = conditions_for("Monthly Close Exception")
bank_cert_conditions = conditions_for("Monthly Close Bank Certification")
reopen_conditions = conditions_for("Monthly Close Reopen Request")
event_conditions = conditions_for("Monthly Close Event")
snapshot_conditions = conditions_for("Monthly Close Snapshot")
