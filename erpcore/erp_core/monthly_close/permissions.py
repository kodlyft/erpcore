# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Company scoping and role checks for every monthly close record.

Frappe already applies Company User Permissions to Link fields, but only when
the permission applies to the doctype in question. These hooks make the rule
explicit and uniform: a user restricted to Company A never sees, exports or acts
on Company B's close, tasks, findings, evidence, events or lock records,
whichever API they come through.
"""

import frappe
from frappe import _

from erpcore.erp_core.monthly_close.constants import (
	ROLE_AUDITOR,
	ROLE_MANAGER,
	ROLE_PREPARER,
	ROLE_REVIEWER,
)

UNRESTRICTED_ROLES = ("System Manager",)


def allowed_companies(user: str | None = None, doctype: str | None = None) -> list[str] | None:
	"""Companies the user may see, or None when they are not restricted."""
	user = user or frappe.session.user
	if user == "Administrator" or set(frappe.get_roles(user)) & set(UNRESTRICTED_ROLES):
		return None

	rows = frappe.defaults.get_user_permissions(user).get("Company") or []
	companies = [
		row.get("doc")
		for row in rows
		if row.get("doc") and (not row.get("applicable_for") or row.get("applicable_for") == doctype)
	]
	return companies or None


def has_company_access(company: str, user: str | None = None, doctype: str | None = None) -> bool:
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


def require_company_access(company: str) -> None:
	if not has_company_access(company):
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
def _conditions_for(doctype):
	def conditions(user=None, doctype_=doctype):
		return query_conditions(doctype_, user)

	return conditions


monthly_close_conditions = _conditions_for("Monthly Close")
policy_conditions = _conditions_for("Monthly Close Policy")
task_conditions = _conditions_for("Monthly Close Task")
check_run_conditions = _conditions_for("Monthly Close Check Run")
exception_conditions = _conditions_for("Monthly Close Exception")
bank_cert_conditions = _conditions_for("Monthly Close Bank Certification")
reopen_conditions = _conditions_for("Monthly Close Reopen Request")
event_conditions = _conditions_for("Monthly Close Event")
snapshot_conditions = _conditions_for("Monthly Close Snapshot")
