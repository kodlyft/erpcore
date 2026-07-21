# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""
Fixture endpoints the Cypress suite calls before each spec.
"""

import frappe
from frappe import _
from frappe.tests.utils import whitelist_for_tests
from frappe.utils import cint, flt, nowdate, nowtime

COMPANY = "_Test Company"
BANK_ACCOUNT = "_Test Cypress Current - _Test Cheque Bank"
GATE = "_Test Main Gate"
UOM = "_Test UOM"
SUPPLIER = "_Test Supplier"
BANK_LEDGER = "HDFC - _TC"
PAYABLE_LEDGER = "Creditors - _TC"
GUARD_USER = "_test_gate_keeper@example.com"

VOID_REASON = "Spoiled / Misprint"


@whitelist_for_tests()
def seed():
	"""Names every spec needs, and a loud failure if the masters are missing."""
	missing = [
		label
		for label, doctype, name in (
			("Company", "Company", COMPANY),
			("Bank Account", "Bank Account", BANK_ACCOUNT),
			("Gate", "Gate", GATE),
			("UOM", "UOM", UOM),
			("Guard user", "User", GUARD_USER),
		)
		if not frappe.db.exists(doctype, name)
	]

	if missing:
		frappe.throw(
			_(
				"Missing test masters: {0}. Run: bench --site {1} execute erpcore.tests.utils.seed_masters"
			).format(", ".join(missing), frappe.local.site)
		)

	return {
		"company": COMPANY,
		"bank_account": BANK_ACCOUNT,
		"gate": GATE,
		"uom": UOM,
		"supplier": SUPPLIER,
		"guard_user": GUARD_USER,
		"void_reason": VOID_REASON,
	}


@whitelist_for_tests()
def make_cheque_book(number_of_leaves=3, submit=1):
	"""A cheque book numbered past whatever the account already holds."""
	number_of_leaves = cint(number_of_leaves) or 3

	highest = (
		frappe.db.sql(
			"""select max(cheque_number) from `tabCheque Leaf` where bank_account = %s""",
			BANK_ACCOUNT,
		)[0][0]
		or 100
	)

	book = frappe.get_doc(
		{
			"doctype": "Cheque Book",
			"company": COMPANY,
			"bank_account": BANK_ACCOUNT,
			"issue_date": nowdate(),
			"prefix": "CHQ",
			"padding_length": 6,
			"starting_number": highest + 1,
			"number_of_leaves": number_of_leaves,
		}
	)
	book.insert(ignore_permissions=True)

	if cint(submit):
		book.submit()

	return {
		"name": book.name,
		"starting_number": book.starting_number,
		"first_cheque_no": book.first_cheque_no,
		"leaves": frappe.get_all(
			"Cheque Leaf",
			filters={"cheque_book": book.name},
			fields=["name", "cheque_no", "status"],
			order_by="cheque_number",
		),
	}


@whitelist_for_tests()
def make_gate_pass(returnable=0, submit=1, verified=0, direction="Outward"):
	"""A gate pass carrying a single free-text row."""
	doc = frappe.get_doc(
		{
			"doctype": "Gate Pass",
			"company": COMPANY,
			"direction": direction,
			"returnable": cint(returnable),
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"party_type": "Supplier",
			"party": SUPPLIER,
			"party_name": SUPPLIER,
			"carrying_by": "_Test Driver",
			"purpose": "Cypress",
			"gate": GATE,
			"items": [
				{
					"is_stock_item": 0,
					"item_description": "_Test Crate",
					"qty": 10,
					"uom": UOM,
					"rate": 100,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)

	if cint(submit):
		doc.submit()

	if cint(verified):
		doc.db_set({"guard_verified": 1, "checked_by": frappe.session.user})
		doc.reload()
		doc.set_status(update=True)

	return {"name": doc.name, "item_row": doc.items[0].name, "status": doc.status}


@whitelist_for_tests()
def make_payment_entry(amount=500):
	"""A draft outgoing Payment Entry drawn on the test bank account.

	Built here rather than in the browser because ERPNext's own party/account
	plumbing is what makes a Payment Entry valid; the spec only cares about the
	cheque leaf that erpcore adds on top.
	"""
	payment = frappe.new_doc("Payment Entry")
	payment.payment_type = "Pay"
	payment.company = COMPANY
	payment.party_type = "Supplier"
	payment.party = SUPPLIER
	payment.paid_from = BANK_LEDGER
	payment.paid_to = PAYABLE_LEDGER
	payment.paid_amount = flt(amount)
	payment.reference_no = "CYPRESS"
	payment.reference_date = nowdate()
	payment.setup_party_account_field()
	payment.set_missing_values()
	payment.received_amount = payment.paid_amount / (payment.target_exchange_rate or 1)
	payment.insert(ignore_permissions=True)

	return {"name": payment.name}


@whitelist_for_tests()
def set_setting(doctype, fieldname, value):
	"""Flip a single field on Cheque Settings / Gate Pass Settings."""
	if doctype not in ("Cheque Settings", "Gate Pass Settings"):
		frappe.throw(_("{0} is not a settings doctype this helper may touch.").format(doctype))

	frappe.db.set_single_value(doctype, fieldname, value)
	frappe.clear_cache()
