# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpcore.erp_core.cheque_constants import DEFAULT_VOID_REASONS


def after_migrate():
	setup_custom_fields()
	setup_roles()
	seed_void_reasons()
	create_indexes()


def setup_custom_fields():
	fields = {}

	for doctype, definitions in get_custom_fields().items():
		if not frappe.db.exists("DocType", doctype):
			continue

		installable = [d for d in definitions if _target_exists(d)]
		if installable:
			fields[doctype] = installable

	if fields:
		create_custom_fields(fields, update=True)


def _target_exists(definition):
	"""True unless this is a Link/Table field whose target doctype is missing."""
	if definition.get("fieldtype") not in ("Link", "Table", "Table MultiSelect"):
		return True

	return bool(frappe.db.exists("DocType", definition.get("options")))


def get_custom_fields():
	return {
		"Payment Entry": [
			{
				"fieldname": "cheque_leaf",
				"fieldtype": "Link",
				"label": "Cheque Leaf",
				"options": "Cheque Leaf",
				"insert_after": "reference_no",
				"no_copy": 1,
				"depends_on": "eval:doc.payment_type!='Receive'",
				"description": "Pick an unused leaf from a company cheque book. Fills in Cheque/Reference No and Date.",
			},
			{
				"fieldname": "cheque_leaf_status",
				"fieldtype": "Data",
				"label": "Cheque Status",
				"insert_after": "cheque_leaf",
				"fetch_from": "cheque_leaf.status",
				"read_only": 1,
				"no_copy": 1,
				"depends_on": "cheque_leaf",
				"translatable": 0,
			},
			{
				"fieldname": "cheque_receipt",
				"fieldtype": "Link",
				"label": "Cheque Receipt",
				"options": "Cheque Receipt",
				"insert_after": "cheque_leaf_status",
				"no_copy": 1,
				"depends_on": "eval:doc.payment_type=='Receive'",
				"description": "The customer cheque being cleared by this entry.",
			},
		],
		"Journal Entry": [
			{
				"fieldname": "cheque_leaf",
				"fieldtype": "Link",
				"label": "Cheque Leaf",
				"options": "Cheque Leaf",
				"insert_after": "cheque_date",
				"no_copy": 1,
				"description": "Pick an unused leaf from a company cheque book. Fills in Reference Number and Date.",
			},
			{
				"fieldname": "cheque_leaf_status",
				"fieldtype": "Data",
				"label": "Cheque Status",
				"insert_after": "cheque_leaf",
				"fetch_from": "cheque_leaf.status",
				"read_only": 1,
				"no_copy": 1,
				"depends_on": "cheque_leaf",
				"translatable": 0,
			},
		],
	}


def setup_roles():
	for role_name in ("Gate Pass User", "Gate Keeper", "Gate Pass Manager"):
		if not frappe.db.exists("Role", role_name):
			continue

		role = frappe.get_doc("Role", role_name)
		if not role.desk_access:
			role.desk_access = 1
			role.save(ignore_permissions=True)


def seed_void_reasons():
	if not frappe.db.exists("DocType", "Cheque Void Reason"):
		return

	for reason_name, applies_to, description in DEFAULT_VOID_REASONS:
		if frappe.db.exists("Cheque Void Reason", reason_name):
			continue

		frappe.get_doc(
			{
				"doctype": "Cheque Void Reason",
				"reason_name": reason_name,
				"applies_to": applies_to,
				"description": description,
			}
		).insert(ignore_permissions=True)


def create_indexes():
	frappe.db.add_index("Cheque Leaf", ["cheque_book", "cheque_number"], "cheque_book_number")
	frappe.db.add_unique("Cheque Leaf", ["reference_doctype", "reference_name"], "leaf_voucher_unique")
