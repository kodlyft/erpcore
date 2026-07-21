# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, nowdate

from erpcore.erp_core.cheque_constants import (
	LEAF_CLEARED,
	LEAF_ISSUED,
	VOUCHER_DOCTYPES,
	VOUCHER_FIELDS,
)


def reconcile_cheque_leaf_status():
	"""
	Bring every leaf's status back in line with its voucher's clearance date.

	Set-based: one query per voucher doctype, and on a healthy site both return
	zero rows.
	"""
	if not frappe.get_cached_value(
		"Cheque Settings", "Cheque Settings", "auto_set_cleared_from_clearance_date"
	):
		return

	for voucher_doctype in VOUCHER_DOCTYPES:
		_reconcile_against(voucher_doctype)


def _reconcile_against(voucher_doctype):
	"""
	Find leaves whose status or clearance date disagrees with their voucher.

	Built with the query builder because the voucher table name is dynamic: it
	cannot be a bound parameter, and interpolating it into raw SQL is the pattern
	that invites injection bugs.
	"""
	leaf = frappe.qb.DocType("Cheque Leaf")
	voucher = frappe.qb.DocType(voucher_doctype)

	mismatched = (
		frappe.qb.from_(leaf)
		.inner_join(voucher)
		.on(voucher.name == leaf.reference_name)
		.select(
			leaf.name.as_("leaf"),
			leaf.status.as_("leaf_status"),
			leaf.clearance_date.as_("leaf_clearance_date"),
			voucher.clearance_date.as_("voucher_clearance_date"),
		)
		.where(leaf.reference_doctype == voucher_doctype)
		.where(leaf.status.isin([LEAF_ISSUED, LEAF_CLEARED]))
		.where(
			(voucher.clearance_date.notnull() & (leaf.status == LEAF_ISSUED))
			| (voucher.clearance_date.isnull() & (leaf.status == LEAF_CLEARED))
			| (voucher.clearance_date != leaf.clearance_date)
		)
		.run(as_dict=True)
	)

	for row in mismatched:
		updates = {"clearance_date": row.voucher_clearance_date}

		if row.voucher_clearance_date and row.leaf_status == LEAF_ISSUED:
			updates["status"] = LEAF_CLEARED
		elif not row.voucher_clearance_date and row.leaf_status == LEAF_CLEARED:
			updates["status"] = LEAF_ISSUED

		frappe.db.set_value("Cheque Leaf", row.leaf, updates, update_modified=False)


def notify_pdc_due():
	"""Warn the owners of post-dated cheques that are about to come due."""
	if not frappe.db.exists("DocType", "Cheque Receipt"):
		return

	settings = frappe.get_cached_doc("Cheque Settings")
	lead_days = settings.pdc_alert_days_before or 3
	due_by = add_days(nowdate(), lead_days)

	due = frappe.get_all(
		"Cheque Receipt",
		filters={
			"docstatus": 1,
			"status": ["in", ["Received", "Deposited"]],
			"cheque_date": ["<=", due_by],
		},
		fields=["name", "party", "cheque_no", "cheque_date", "amount", "owner"],
	)

	for receipt in due:
		frappe.publish_realtime(
			"cheque_receipt_due",
			message={"name": receipt.name, "cheque_no": receipt.cheque_no},
			user=receipt.owner,
		)


def update_gate_pass_overdue_status():
	"""Flag returnable gate passes whose material is past its return date."""
	overdue = frappe.get_all(
		"Gate Pass",
		filters={
			"docstatus": 1,
			"returnable": 1,
			"status": ["in", ["Exited", "Partly Returned"]],
			"expected_return_date": ["<", nowdate()],
		},
		pluck="name",
	)

	for name in overdue:
		frappe.db.set_value("Gate Pass", name, "status", "Overdue", update_modified=False)


def expire_stale_visitor_passes():
	"""Close out visitor passes for visitors who never turned up."""
	if not frappe.db.exists("DocType", "Visitor Gate Pass"):
		return

	stale = frappe.get_all(
		"Visitor Gate Pass",
		filters={"docstatus": 1, "status": "Approved", "visit_date": ["<", nowdate()]},
		pluck="name",
	)

	for name in stale:
		frappe.db.set_value("Visitor Gate Pass", name, "status", "Expired", update_modified=False)


def notify_overdue_gate_pass_returns():
	"""Daily digest of material still outside past its due date."""
	settings = frappe.get_cached_doc("Gate Pass Settings")
	if not settings.notify_roles:
		return

	overdue = frappe.get_all(
		"Gate Pass",
		filters={"docstatus": 1, "returnable": 1, "status": "Overdue"},
		fields=["name", "party", "expected_return_date", "per_returned"],
	)
	if not overdue:
		return

	recipients = _users_with_roles([row.role for row in settings.notify_roles])
	if not recipients:
		return

	frappe.sendmail(
		recipients=recipients,
		subject=frappe._("{0} gate passes have material overdue for return").format(len(overdue)),
		template=None,
		message=_overdue_digest_html(overdue),
		reference_doctype="Gate Pass",
	)


def _users_with_roles(roles):
	if not roles:
		return []

	return frappe.get_all(
		"Has Role",
		filters={"role": ["in", roles], "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)


def _overdue_digest_html(rows):
	lines = [
		"<table border='1' cellpadding='6' cellspacing='0'>",
		"<tr><th>Gate Pass</th><th>Party</th><th>Due</th><th>% Returned</th></tr>",
	]
	for row in rows:
		lines.append(
			f"<tr><td>{frappe.utils.escape_html(row.name)}</td>"
			f"<td>{frappe.utils.escape_html(row.party or '')}</td>"
			f"<td>{frappe.format(row.expected_return_date, 'Date')}</td>"
			f"<td>{frappe.utils.flt(row.per_returned, 2)}%</td></tr>"
		)
	lines.append("</table>")
	return "".join(lines)
