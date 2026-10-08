# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Append-only audit trail for closes."""

import json

import frappe
from frappe.utils import now_datetime


def log_event(
	close,
	event_type: str,
	from_state: str | None = None,
	to_state: str | None = None,
	details: dict | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> str:
	"""Record what happened to a close. Events are inserted, never updated or deleted."""
	event = frappe.get_doc(
		{
			"doctype": "Monthly Close Event",
			"monthly_close": close.name,
			"company": close.company,
			"revision": close.revision,
			"event_type": event_type,
			"actor": frappe.session.user,
			"event_time": now_datetime(),
			"from_state": from_state,
			"to_state": to_state,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"details": json.dumps(details, default=str, sort_keys=True, indent=1) if details else None,
		}
	)
	event.insert(ignore_permissions=True)
	return event.name
