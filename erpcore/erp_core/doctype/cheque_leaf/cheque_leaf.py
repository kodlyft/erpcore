# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate

from erpcore.erp_core.cheque_constants import (
	LEAF_BOUNCED,
	LEAF_CLEARED,
	LEAF_ISSUED,
	LEAF_LOST,
	LEAF_RESERVED,
	LEAF_STOPPED,
	LEAF_UNUSED,
	LEAF_VOID,
)


class ChequeLeaf(Document):
	def autoname(self):
		self.name = f"{self.cheque_book}-{self.cheque_no}"

	def on_update(self):
		self.update_book_counts()

	def on_trash(self):
		if self.status != LEAF_UNUSED:
			frappe.throw(
				_("Cheque {0} cannot be deleted because its status is {1}.").format(
					frappe.bold(self.cheque_no), frappe.bold(_(self.status))
				)
			)

	def update_book_counts(self):
		from erpcore.erp_core.doctype.cheque_book.cheque_book import update_counts

		if self.cheque_book and frappe.db.exists("Cheque Book", self.cheque_book):
			update_counts(self.cheque_book)

	def _require_manage_permission(self):
		if not frappe.has_permission("Cheque Leaf", "write"):
			frappe.throw(_("You are not permitted to change cheque status."), frappe.PermissionError)

	def _resolve_reason(self, reason):
		if frappe.db.get_single_value("Cheque Settings", "require_void_reason") and not reason:
			frappe.throw(_("A reason is required."), title=_("Reason Required"))

		if reason and not frappe.db.exists("Cheque Void Reason", reason):
			frappe.throw(_("Cheque Void Reason {0} does not exist.").format(frappe.bold(reason)))

		return reason

	def _apply_action(self, status, reason, remarks):
		self.db_set(
			{
				"status": status,
				"void_reason": reason,
				"void_date": nowdate(),
				"voided_by": frappe.session.user,
				"void_remarks": remarks,
			}
		)
		self.update_book_counts()

	@frappe.whitelist()
	def void_leaf(self, reason: str | None = None, remarks: str | None = None):
		"""Void an unused or reserved cheque, e.g. a misprint."""
		self._require_manage_permission()

		if self.status not in (LEAF_UNUSED, LEAF_RESERVED):
			frappe.throw(
				_("Only unused or reserved cheques can be voided. This one is {0}.").format(
					frappe.bold(_(self.status))
				)
			)

		if self.status == LEAF_RESERVED and self.reference_name:
			frappe.throw(
				_("Cheque {0} is reserved by draft {1} {2}. Clear it there first.").format(
					frappe.bold(self.cheque_no), self.reference_doctype, frappe.bold(self.reference_name)
				)
			)

		self._apply_action(LEAF_VOID, self._resolve_reason(reason), remarks)
		return self.status

	@frappe.whitelist()
	def mark_lost(self, reason: str | None = None, remarks: str | None = None):
		"""Record a cheque that went missing before it was ever issued."""
		self._require_manage_permission()

		if self.status not in (LEAF_UNUSED, LEAF_RESERVED):
			frappe.throw(_("Only unused cheques can be marked lost."))

		self._apply_action(LEAF_LOST, self._resolve_reason(reason), remarks)
		return self.status

	@frappe.whitelist()
	def mark_stopped(self, reason: str | None = None, remarks: str | None = None):
		"""Stop payment on a cheque already handed over."""
		self._require_manage_permission()

		if self.status != LEAF_ISSUED:
			frappe.throw(
				_("Only issued cheques can be stopped. This one is {0}.").format(frappe.bold(_(self.status)))
			)

		self._apply_action(LEAF_STOPPED, self._resolve_reason(reason), remarks)
		return self.status

	@frappe.whitelist()
	def mark_bounced(self, reason: str | None = None, remarks: str | None = None):
		"""Record that the bank returned this cheque unpaid."""
		self._require_manage_permission()

		if self.status not in (LEAF_ISSUED, LEAF_CLEARED):
			frappe.throw(_("Only issued or cleared cheques can be marked bounced."))

		self._apply_action(LEAF_BOUNCED, self._resolve_reason(reason), remarks)
		return self.status

	@frappe.whitelist()
	def revert_to_unused(self, remarks: str | None = None):
		"""
		Return a voided or lost cheque to the pool.

		Gated on ``allow_void_leaf_reuse`` because a voided number staying
		permanently traceable is the whole audit point of the register.
		"""
		self._require_manage_permission()

		if self.status not in (LEAF_VOID, LEAF_LOST):
			frappe.throw(_("Only voided or lost cheques can be returned to the pool."))

		if self.status == LEAF_VOID and not frappe.db.get_single_value(
			"Cheque Settings", "allow_void_leaf_reuse"
		):
			frappe.throw(
				_(
					"Reusing voided cheque numbers is disabled. Enable Allow Void Leaf Reuse in Cheque Settings if your bank stationery genuinely permits it."
				),
				title=_("Reuse Not Allowed"),
			)

		self.db_set(
			{
				"status": LEAF_UNUSED,
				"reference_doctype": None,
				"reference_name": None,
				"void_reason": None,
				"void_date": None,
				"voided_by": None,
				"void_remarks": remarks,
				"amount": 0,
				"party_type": None,
				"party": None,
				"payee_name": None,
				"clearance_date": None,
			}
		)
		self.update_book_counts()
		return self.status


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_available_leaves(
	doctype: str,
	txt: str,
	searchfield: str,
	start: int,
	page_len: int,
	filters: dict | str | None = None,
):
	frappe.has_permission("Cheque Leaf", "read", throw=True)

	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})

	leaf = frappe.qb.DocType("Cheque Leaf")
	book = frappe.qb.DocType("Cheque Book")
	pattern = f"%{txt}%"

	query = (
		frappe.qb.from_(leaf)
		.inner_join(book)
		.on(book.name == leaf.cheque_book)
		.select(leaf.name, leaf.cheque_no, leaf.cheque_book, leaf.bank)
		.where(book.docstatus == 1)
		.where(book.status == "Active")
		.where(
			(leaf.status == LEAF_UNUSED)
			| ((leaf.status == LEAF_RESERVED) & (leaf.reference_name == filters.get("voucher_name")))
		)
		.where(leaf.name.like(pattern) | leaf.cheque_no.like(pattern) | leaf.cheque_book.like(pattern))
		.orderby(leaf.cheque_number)
		.limit(page_len)
		.offset(start)
	)

	if filters.get("company"):
		query = query.where(leaf.company == filters["company"])

	if filters.get("bank_account"):
		query = query.where(leaf.bank_account == filters["bank_account"])

	return query.run()
