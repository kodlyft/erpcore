# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Daily jobs: lock integrity, recovery of stuck work and deduplicated reminders.

None of these change a close's state or a lock. Integrity failures are logged
as events and reported; a person decides what to do.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, getdate, now_datetime, nowdate

from erpcore.erp_core.monthly_close import native_lock, txn
from erpcore.erp_core.monthly_close.constants import (
	CHECK_RUN_DOCTYPE,
	CLOSE_DOCTYPE,
	EVENT_DOCTYPE,
	IN_PROGRESS,
	LOCKED_STATES,
	RUN_FAILED,
	RUN_QUEUED,
	RUN_RUNNING,
	SETTINGS_DOCTYPE,
	TASK_DOCTYPE,
)
from erpcore.erp_core.monthly_close.events import log_event
from erpcore.erp_core.monthly_close.notifications import users_with_roles

STUCK_AFTER_HOURS = 6


def check_lock_integrity():
	"""Runs even when the module is disabled: existing locks must still be watched."""
	for name in frappe.get_all(CLOSE_DOCTYPE, filters={"state": ["in", list(LOCKED_STATES)]}, pluck="name"):
		close = frappe.get_doc(CLOSE_DOCTYPE, name)
		health = native_lock.lock_health(close)
		if health["status"] == "Healthy":
			continue

		already = frappe.db.exists(
			EVENT_DOCTYPE,
			{
				"monthly_close": name,
				"event_type": "Lock Integrity Failed",
				"event_time": [">=", getdate(nowdate())],
			},
		)
		if already:
			continue

		log_event(close, "Lock Integrity Failed", details=health)
		txn.commit()


def recover_stuck_runs():
	"""Mark runs a dead worker left behind as Failed so a new run can be requested."""
	cutoff = add_to_date(now_datetime(), hours=-STUCK_AFTER_HOURS)
	for name in frappe.get_all(
		CHECK_RUN_DOCTYPE,
		filters={"status": ["in", [RUN_QUEUED, RUN_RUNNING]], "modified": ["<", cutoff]},
		pluck="name",
	):
		frappe.db.set_value(
			CHECK_RUN_DOCTYPE,
			name,
			{
				"status": RUN_FAILED,
				"error": _("The worker did not finish within {0} hours.").format(STUCK_AFTER_HOURS),
			},
			update_modified=False,
		)
	txn.commit()


def send_task_reminders():
	"""One digest per user per day, only for users who can see the company."""
	if not cint(frappe.db.get_single_value(SETTINGS_DOCTYPE, "enabled")):
		return

	today = getdate(nowdate())
	for close in frappe.get_all(
		CLOSE_DOCTYPE, filters={"state": IN_PROGRESS}, fields=["name", "company", "revision", "policy"]
	):
		lead = (
			cint(frappe.db.get_value("Monthly Close Policy", close.policy, "reminder_days_before_due")) or 2
		)
		already_sent = frappe.db.exists(
			EVENT_DOCTYPE,
			{
				"monthly_close": close.name,
				"event_type": "Reminders Sent",
				"event_time": [">=", today],
			},
		)
		if already_sent:
			continue

		tasks = frappe.get_all(
			TASK_DOCTYPE,
			filters={
				"monthly_close": close.name,
				"revision": close.revision,
				"status": "Open",
				"due_date": ["<=", add_days(today, lead)],
			},
			fields=["name", "title", "due_date", "assigned_to", "assigned_role"],
		)
		if not tasks:
			continue

		by_user: dict[str, list] = {}
		for task in tasks:
			users = (
				[task.assigned_to]
				if task.assigned_to
				else users_with_roles([task.assigned_role], close.company)
			)
			for user in users:
				if user and may_receive(close.company, user):
					by_user.setdefault(user, []).append(task)

		for user, user_tasks in by_user.items():
			try:
				frappe.sendmail(
					recipients=[user],
					subject=_("{0}: {1} close tasks due").format(close.name, len(user_tasks)),
					message="<br>".join(
						f"{frappe.utils.escape_html(t.title)} — {frappe.format(t.due_date, 'Date')}"
						for t in user_tasks
					),
					reference_doctype=CLOSE_DOCTYPE,
					reference_name=close.name,
				)
			except Exception:
				frappe.log_error(title=f"erpcore: reminder failed for {close.name}")

		doc = frappe.get_doc(CLOSE_DOCTYPE, close.name)
		log_event(doc, "Reminders Sent", details={"date": today, "users": sorted(by_user)})
		txn.commit()


def may_receive(company: str, user: str) -> bool:
	"""Enabled system user with access to the company. Re-checked for directly assigned users too."""
	from erpcore.erp_core.monthly_close.permissions import has_company_access

	row = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not row or not row.enabled or row.user_type != "System User":
		return False
	return has_company_access(company, user, CLOSE_DOCTYPE)
