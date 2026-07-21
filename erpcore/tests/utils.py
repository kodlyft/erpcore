# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Fixtures shared by the erpcore test suite."""

import frappe


def _relax_password_policy():
	"""ERPNext's bootstrap creates users whose password is literally "password"."""
	if frappe.db.get_single_value("System Settings", "enable_password_policy"):
		frappe.db.set_single_value("System Settings", "enable_password_policy", 0)
		frappe.clear_cache()


_relax_password_policy()

from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import add_days, nowdate, nowtime

from erpcore.erp_core.cheque_constants import LEAF_UNUSED

TEST_COMPANY = "_Test Company"
OTHER_COMPANY = "_Test Company 1"
TEST_ACCOUNT = "_Test Bank - _TC"
OTHER_ACCOUNT = "_Test Bank USD - _TC"
TEST_BANK = "_Test Cheque Bank"
TEST_BANK_ACCOUNT = f"_Test Cheque Current - {TEST_BANK}"
OTHER_BANK_ACCOUNT = f"_Test Cheque Savings - {TEST_BANK}"
CYPRESS_ACCOUNT = "HDFC - _TC"
CYPRESS_BANK_ACCOUNT = f"_Test Cypress Current - {TEST_BANK}"
TEST_GATE = "_Test Main Gate"
TEST_ITEM = "_Test Item"
TEST_UOM = "_Test UOM"
TEST_SUPPLIER = "_Test Supplier"

GUARD_USER = "_test_gate_keeper@example.com"
PLAIN_USER = "_test_no_roles@example.com"


class ERPCoreTestCase(ERPNextTestSuite):
	"""Base class for every erpcore test."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_masters()


def seed_masters():
	"""Create the committed masters the suite leans on. Idempotent."""
	from erpcore.setup import seed_void_reasons

	seed_void_reasons()
	backfill_accounting_dimensions()
	make_bank_account("_Test Cheque Current", TEST_ACCOUNT)
	make_bank_account("_Test Cheque Savings", OTHER_ACCOUNT)
	make_bank_account("_Test Cypress Current", CYPRESS_ACCOUNT)
	make_gate()
	make_users()
	make_outgoing_email_account()


def backfill_accounting_dimensions():
	"""Give every Accounting Dimension the columns ERPNext expects it to have."""
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
		make_dimension_in_accounting_doctypes,
	)

	for dimension in frappe.get_all("Accounting Dimension", fields=["name", "document_type"]):
		fieldname = frappe.scrub(dimension.document_type)
		if frappe.db.exists("Custom Field", {"dt": "Budget", "fieldname": fieldname}):
			continue

		make_dimension_in_accounting_doctypes(doc=frappe.get_doc("Accounting Dimension", dimension.name))


def make_bank_account(account_name, account, company=TEST_COMPANY, is_company_account=1, disabled=0):
	"""A bank account wired to one of the bootstrap's ``account_type = Bank`` accounts."""
	if not frappe.db.exists("Bank", TEST_BANK):
		frappe.get_doc({"doctype": "Bank", "bank_name": TEST_BANK}).insert(ignore_permissions=True)

	name = f"{account_name} - {TEST_BANK}"
	if frappe.db.exists("Bank Account", name):
		return name

	frappe.get_doc(
		{
			"doctype": "Bank Account",
			"account_name": account_name,
			"bank": TEST_BANK,
			"account": account,
			"company": company,
			"is_company_account": is_company_account,
			"disabled": disabled,
		}
	).insert(ignore_permissions=True)

	return name


def make_gate(company=TEST_COMPANY):
	if not frappe.db.exists("Gate", TEST_GATE):
		frappe.get_doc(
			{"doctype": "Gate", "gate_name": TEST_GATE, "company": company, "gate_type": "Material"}
		).insert(ignore_permissions=True)

	return TEST_GATE


def make_outgoing_email_account():
	"""A default outgoing account, so the overdue digest has somewhere to queue."""
	if frappe.db.exists("Email Account", {"default_outgoing": 1}):
		return

	frappe.get_doc(
		{
			"doctype": "Email Account",
			"email_account_name": "_Test Outgoing",
			"email_id": "_test_outgoing@example.com",
			"smtp_server": "test.example.com",
			"enable_outgoing": 1,
			"default_outgoing": 1,
			"no_smtp_authentication": 1,
		}
	).insert(ignore_permissions=True)


def make_users():
	"""A user holding Gate Keeper, and one holding nothing at all."""
	_make_user(GUARD_USER, "_Test", "Gate Keeper", roles=["Gate Keeper"])
	_make_user(PLAIN_USER, "_Test", "No Roles", roles=[])


def _make_user(email, first_name, last_name, roles):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"last_name": last_name,
				"send_welcome_email": 0,
			}
		)
		user.insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", email)

	existing = {row.role for row in user.get("roles")}
	wanted = set(roles)
	if wanted - existing:
		for role in wanted - existing:
			user.append("roles", {"role": role})
		user.save(ignore_permissions=True)

	return email


def make_cheque_book(**args):
	"""A submitted cheque book with its leaves generated, unless ``submit=False``."""
	args = frappe._dict(args)

	book = frappe.get_doc(
		{
			"doctype": "Cheque Book",
			"company": args.company or TEST_COMPANY,
			"bank_account": args.bank_account or TEST_BANK_ACCOUNT,
			"issue_date": args.issue_date or nowdate(),
			"prefix": args.prefix if args.prefix is not None else "CHQ",
			"suffix": args.suffix,
			"padding_length": args.padding_length or 6,
			"starting_number": args.starting_number if args.starting_number is not None else 101,
			"number_of_leaves": args.number_of_leaves if args.number_of_leaves is not None else 5,
			"book_reference": args.book_reference,
		}
	)
	book.insert(ignore_permissions=True)

	if args.get("submit") is not False:
		book.submit()
		book.reload()

	return book


def get_leaves(cheque_book):
	return frappe.get_all(
		"Cheque Leaf",
		filters={"cheque_book": cheque_book},
		fields=["name", "cheque_no", "cheque_number", "status"],
		order_by="cheque_number",
	)


def get_leaf(cheque_book, index=0):
	"""The nth leaf of a book, as a full document."""
	return frappe.get_doc("Cheque Leaf", get_leaves(cheque_book)[index].name)


def unused_leaf(cheque_book, index=0):
	leaves = [row for row in get_leaves(cheque_book) if row.status == LEAF_UNUSED]
	return leaves[index].name


def make_gate_pass(**args):
	"""A gate pass carrying one non-stock row, submitted unless ``submit=False``."""
	args = frappe._dict(args)

	items = args.get("items")
	if items is None:
		items = [
			{
				"is_stock_item": 0,
				"item_description": "_Test Crate",
				"qty": 10,
				"uom": TEST_UOM,
				"rate": 100,
			}
		]

	doc = frappe.get_doc(
		{
			"doctype": "Gate Pass",
			"company": args.company or TEST_COMPANY,
			"direction": args.direction or "Outward",
			"returnable": 1 if args.returnable else 0,
			"posting_date": args.posting_date or nowdate(),
			"posting_time": args.posting_time or nowtime(),
			"party_type": args.party_type,
			"party": args.party,
			"party_name": args.party_name or "_Test Carrier",
			"carrying_by": args.carrying_by or "_Test Driver",
			"purpose": args.purpose or "Testing",
			"gate": args.gate or TEST_GATE,
			"expected_return_date": args.expected_return_date,
			"items": items,
		}
	)
	doc.insert(ignore_permissions=True)

	if args.get("submit") is not False:
		doc.submit()
		doc.reload()

	return doc


def make_gate_pass_return(gate_pass, qty_map=None, submit=True):
	"""Build a return off ``gate_pass`` via the app's own mapper."""
	from erpcore.erp_core.doctype.gate_pass_return.gate_pass_return import (
		make_gate_pass_return as map_return,
	)

	doc = map_return(gate_pass.name if hasattr(gate_pass, "name") else gate_pass)

	if qty_map:
		for row in doc.items:
			if row.gate_pass_item in qty_map:
				row.returned_qty = qty_map[row.gate_pass_item]

	doc.insert(ignore_permissions=True)

	if submit:
		doc.submit()
		doc.reload()

	return doc


def overdue_date():
	return add_days(nowdate(), -1)
