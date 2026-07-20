# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpcore.erp_core.cheque_constants import (
	LEAF_AVAILABLE_STATUSES,
	LEAF_CLEARED,
	LEAF_ISSUED,
	LEAF_RESERVED,
	LEAF_UNUSED,
	LEAF_VOID,
	VOID_REASON_CANCELLED_VOUCHER,
	VOUCHER_FIELDS,
)


def get_cheque_settings():
	return frappe.get_cached_doc("Cheque Settings")


def _voucher_fields(doctype):
	"""Cheque number/date fieldnames for a voucher doctype, or None if unsupported."""
	return VOUCHER_FIELDS.get(doctype)


def voucher_validate(doc, method=None):
	if not _voucher_fields(doc.doctype):
		return

	_release_previous_leaf(doc)

	if not doc.get("cheque_leaf"):
		return

	leaf = validate_cheque_leaf(doc)
	apply_leaf_to_voucher(doc, leaf)

	if doc.docstatus == 0 and get_cheque_settings().reserve_leaf_on_draft:
		reserve_leaf(doc, leaf)


def _release_previous_leaf(doc):
	"""If this save changed or cleared ``cheque_leaf``, free the leaf it pointed at."""
	if doc.is_new():
		return

	previous = doc.get_doc_before_save()
	if not previous:
		return

	old_leaf = previous.get("cheque_leaf")
	if old_leaf and old_leaf != doc.get("cheque_leaf"):
		release_leaf(old_leaf, doc)


def validate_cheque_leaf(doc):
	"""Layer 2 of the double-use guard. Returns the leaf's field values."""
	leaf = frappe.db.get_value(
		"Cheque Leaf",
		doc.cheque_leaf,
		[
			"name",
			"status",
			"reference_doctype",
			"reference_name",
			"bank_account",
			"company",
			"cheque_no",
			"cheque_date",
		],
		as_dict=True,
	)

	if not leaf:
		frappe.throw(_("Cheque Leaf {0} does not exist.").format(doc.cheque_leaf))

	owned_by_this_voucher = (leaf.reference_doctype, leaf.reference_name) == (doc.doctype, doc.name)

	if leaf.status not in LEAF_AVAILABLE_STATUSES and not owned_by_this_voucher:
		if leaf.reference_name:
			frappe.throw(
				_("Cheque {0} is already used by {1} {2} (status {3}).").format(
					frappe.bold(leaf.cheque_no),
					leaf.reference_doctype,
					frappe.bold(leaf.reference_name),
					frappe.bold(_(leaf.status)),
				),
				title=_("Cheque Already Used"),
			)
		frappe.throw(
			_("Cheque {0} cannot be used because its status is {1}.").format(
				frappe.bold(leaf.cheque_no), frappe.bold(_(leaf.status))
			),
			title=_("Cheque Not Available"),
		)

	if leaf.status == LEAF_RESERVED and not owned_by_this_voucher and leaf.reference_name:
		frappe.throw(
			_("Cheque {0} is reserved by draft {1} {2}.").format(
				frappe.bold(leaf.cheque_no), leaf.reference_doctype, frappe.bold(leaf.reference_name)
			),
			title=_("Cheque Reserved"),
		)

	if doc.get("company") and leaf.company and leaf.company != doc.company:
		frappe.throw(
			_("Cheque {0} belongs to company {1}, but this document is for {2}.").format(
				frappe.bold(leaf.cheque_no), frappe.bold(leaf.company), frappe.bold(doc.company)
			)
		)

	voucher_bank_account = doc.get("bank_account")
	if voucher_bank_account and leaf.bank_account and leaf.bank_account != voucher_bank_account:
		frappe.throw(
			_("Cheque {0} belongs to bank account {1}, but this document uses {2}.").format(
				frappe.bold(leaf.cheque_no),
				frappe.bold(leaf.bank_account),
				frappe.bold(voucher_bank_account),
			)
		)

	return leaf


def apply_leaf_to_voucher(doc, leaf):
	"""Write the leaf's number and date into the voucher's own reference fields."""
	fields = _voucher_fields(doc.doctype)
	doc.set(fields["number"], leaf.cheque_no)
	if not doc.get(fields["date"]):
		doc.set(fields["date"], leaf.cheque_date or doc.get("posting_date") or nowdate())

	settings = get_cheque_settings()
	if settings.warn_on_postdated_cheque and leaf.cheque_date and doc.get("posting_date"):
		if getdate(leaf.cheque_date) > getdate(doc.posting_date):
			frappe.msgprint(
				_("Cheque {0} is post-dated to {1}.").format(
					frappe.bold(leaf.cheque_no), frappe.bold(frappe.format(leaf.cheque_date, "Date"))
				),
				indicator="orange",
				alert=True,
			)


def reserve_leaf(doc, leaf):
	"""
	Mark a leaf as spoken for by a draft voucher.

	Without this, two users can each save a draft against the same leaf and only
	discover the clash at submit, by which time both have printed a cheque.
	"""
	if leaf.status == LEAF_RESERVED and leaf.reference_name == doc.name:
		return

	frappe.db.set_value(
		"Cheque Leaf",
		leaf.name,
		{
			"status": LEAF_RESERVED,
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
		},
		update_modified=False,
	)


def release_leaf(leaf_name, doc=None):
	"""Return a reserved leaf to the pool. Never touches a consumed leaf."""
	if not leaf_name:
		return

	leaf = frappe.db.get_value(
		"Cheque Leaf", leaf_name, ["status", "reference_doctype", "reference_name"], as_dict=True
	)
	if not leaf or leaf.status != LEAF_RESERVED:
		return

	if doc and (leaf.reference_doctype, leaf.reference_name) != (doc.doctype, doc.name):
		return

	frappe.db.set_value(
		"Cheque Leaf",
		leaf_name,
		{"status": LEAF_UNUSED, "reference_doctype": None, "reference_name": None},
		update_modified=False,
	)


def release_reservation(doc, method=None):
	"""``on_trash`` handler: a deleted draft must not hold a leaf hostage."""
	if _voucher_fields(doc.doctype) and doc.get("cheque_leaf"):
		release_leaf(doc.cheque_leaf, doc)


def voucher_on_submit(doc, method=None):
	"""Consume the leaf. Takes a row lock so a concurrent submit blocks here
	rather than racing to the unique index.
	"""
	if not _voucher_fields(doc.doctype) or not doc.get("cheque_leaf"):
		return

	status = frappe.db.get_value("Cheque Leaf", doc.cheque_leaf, "status", for_update=True)

	if status not in LEAF_AVAILABLE_STATUSES:
		reference = frappe.db.get_value(
			"Cheque Leaf", doc.cheque_leaf, ["reference_doctype", "reference_name"], as_dict=True
		)
		if (reference.reference_doctype, reference.reference_name) != (doc.doctype, doc.name):
			frappe.throw(
				_("Cheque {0} was consumed by another document while this one was open.").format(
					frappe.bold(doc.cheque_leaf)
				),
				title=_("Cheque Already Used"),
			)

	fields = _voucher_fields(doc.doctype)
	frappe.db.set_value(
		"Cheque Leaf",
		doc.cheque_leaf,
		{
			"status": LEAF_ISSUED,
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"issue_date": doc.get("posting_date") or nowdate(),
			"cheque_date": doc.get(fields["date"]),
			"party_type": doc.get("party_type"),
			"party": doc.get("party"),
			"payee_name": doc.get("party_name") or doc.get("party"),
			"amount": _voucher_amount(doc),
			"currency": _voucher_currency(doc),
			"clearance_date": doc.get("clearance_date"),
		},
		update_modified=False,
	)


def _voucher_amount(doc):
	if doc.doctype == "Payment Entry":
		return doc.get("paid_amount")
	return doc.get("total_debit") or doc.get("total_credit")


def _voucher_currency(doc):
	if doc.doctype == "Payment Entry":
		return doc.get("paid_from_account_currency") or doc.get("paid_to_account_currency")
	return frappe.get_cached_value("Company", doc.company, "default_currency") if doc.get("company") else None


def voucher_on_cancel(doc, method=None):
	if not _voucher_fields(doc.doctype) or not doc.get("cheque_leaf"):
		return

	settings = get_cheque_settings()

	if settings.allow_void_leaf_reuse:
		frappe.db.set_value(
			"Cheque Leaf",
			doc.cheque_leaf,
			{
				"status": LEAF_UNUSED,
				"reference_doctype": None,
				"reference_name": None,
				"clearance_date": None,
				"amount": 0,
				"party": None,
				"party_type": None,
				"payee_name": None,
			},
			update_modified=False,
		)
		return

	frappe.db.set_value(
		"Cheque Leaf",
		doc.cheque_leaf,
		{
			"status": LEAF_VOID,
			"void_reason": _cancelled_voucher_reason(),
			"void_date": nowdate(),
			"voided_by": frappe.session.user,
			"void_remarks": _("{0} {1} was cancelled.").format(doc.doctype, doc.name),
		},
		update_modified=False,
	)


def _cancelled_voucher_reason():
	"""The seeded reason, or None if an administrator deleted it."""
	if frappe.db.exists("Cheque Void Reason", VOID_REASON_CANCELLED_VOUCHER):
		return VOID_REASON_CANCELLED_VOUCHER
	return None


def sync_leaf_from_voucher(doc, method=None):
	leaf_name = doc.get("cheque_leaf")
	if not leaf_name:
		return

	leaf = frappe.db.get_value("Cheque Leaf", leaf_name, ["status", "clearance_date"], as_dict=True)
	if not leaf:
		return

	clearance_date = doc.get("clearance_date")
	if leaf.clearance_date == clearance_date:
		return

	if not get_cheque_settings().auto_set_cleared_from_clearance_date:
		return

	updates = {"clearance_date": clearance_date}

	if clearance_date and leaf.status == LEAF_ISSUED:
		updates["status"] = LEAF_CLEARED
	elif not clearance_date and leaf.status == LEAF_CLEARED:
		updates["status"] = LEAF_ISSUED

	frappe.db.set_value("Cheque Leaf", leaf_name, updates, update_modified=False)
