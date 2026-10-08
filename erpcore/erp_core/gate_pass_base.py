# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _

GUARD_ROLES = ("Gate Keeper", "Gate Pass Manager", "System Manager")

MANAGER_ROLES = ("Gate Pass Manager", "System Manager")

GUARD_WRITABLE_FIELDS = frozenset(
	{
		"checked_in_at",
		"checked_out_at",
		"checked_in_by",
		"checked_out_by",
		"checked_by",
		"guard_verified",
		"guard_remarks",
		"badge_number",
		"badge_returned",
		"status",
	}
)


class GatePassBase:
	"""Mixin for the Gate Pass and Visitor Gate Pass controllers."""

	def require_guard_role(self):
		if not set(frappe.get_roles()) & set(GUARD_ROLES):
			frappe.throw(
				_("Only a Gate Keeper can record gate movements."),
				frappe.PermissionError,
			)

	def guard_set(self, values: dict):
		"""
		Write guard-owned fields on a submitted document.
		"""
		unknown = set(values) - GUARD_WRITABLE_FIELDS
		if unknown:
			frappe.throw(_("Fields {0} are not guard-writable.").format(", ".join(sorted(unknown))))

		self.db_set(values)

	def require_submitted(self):
		if self.docstatus != 1:
			frappe.throw(
				_("{0} must be submitted before it can be processed at the gate.").format(_(self.doctype))
			)


def _company_conditions(doctype: str, user: str | None = None):
	"""Limit list views to companies the user is permitted on."""
	user = user or frappe.session.user

	if set(frappe.get_roles(user)) & set(MANAGER_ROLES):
		return None

	companies = frappe.defaults.get_user_permissions(user).get("Company")
	if not companies:
		return None

	allowed = ", ".join(frappe.db.escape(row.get("doc")) for row in companies if row.get("doc"))
	if not allowed:
		return None

	return f"`tab{doctype}`.`company` in ({allowed})"


def gate_pass_query_conditions(user: str | None = None):
	return _company_conditions("Gate Pass", user)


def visitor_gate_pass_query_conditions(user: str | None = None):
	return _company_conditions("Visitor Gate Pass", user)
