# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Post-commit notifications. A failed email never undoes a completed transition or lock."""

import frappe
from frappe import _

from erpcore.erp_core.monthly_close.permissions import has_company_access


def publish_close_update(close_name: str) -> None:
	frappe.publish_realtime(
		"monthly_close_updated",
		message={"name": close_name},
		doctype="Monthly Close",
		docname=close_name,
		after_commit=True,
	)


def users_with_roles(roles, company: str) -> list[str]:
	"""Enabled users holding one of the roles and allowed to see the company."""
	if not roles:
		return []

	users = frappe.get_all(
		"Has Role",
		filters={"role": ["in", list(roles)], "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	enabled = frappe.get_all(
		"User",
		filters={"name": ["in", users or [""]], "enabled": 1, "user_type": "System User"},
		pluck="name",
	)
	return [
		user
		for user in enabled
		if user != "Administrator" and has_company_access(company, user, "Monthly Close")
	]


def notify_after_commit(close, subject: str, message: str, roles=(), users=()) -> None:
	recipients = sorted(set(users_with_roles(roles, close.company)) | {u for u in users if u})

	def send():
		if not recipients:
			return
		try:
			frappe.sendmail(
				recipients=recipients,
				subject=subject,
				message=message,
				reference_doctype="Monthly Close",
				reference_name=close.name,
				now=False,
			)
		except Exception:
			frappe.log_error(title=f"erpcore: monthly close notification failed for {close.name}")

	frappe.db.after_commit.add(send)
	publish_close_update(close.name)


def close_link(close) -> str:
	return frappe.utils.get_link_to_form("Monthly Close", close.name)


def transition_message(close, action: str, reason: str | None = None) -> tuple[str, str]:
	subject = _("{0}: {1} ({2})").format(close.name, _(action), _(close.state))
	body = _("{0} was moved to {1} by {2}.").format(
		close_link(close), frappe.bold(_(close.state)), frappe.session.user
	)
	if reason:
		body += "<br><br>" + frappe.utils.escape_html(reason)
	return subject, body
