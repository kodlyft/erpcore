# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpcore.erp_core.cheque_constants import (
	LEAF_CLEARED,
	LEAF_ISSUED,
	LEAF_UNUSED,
	LEAF_VOID,
)


def compose_cheque_no(prefix, number, padding_length, suffix):
	"""Build the printed cheque number from a book's numbering rules."""
	return f"{prefix or ''}{str(number).zfill(padding_length or 0)}{suffix or ''}"


class ChequeBook(Document):
	def validate(self):
		self.set_defaults()
		self.validate_numbering()
		self.validate_bank_account()
		self.set_previews()

	def set_defaults(self):
		if not self.padding_length:
			self.padding_length = frappe.db.get_single_value("Cheque Settings", "default_padding_length") or 6

	def validate_numbering(self):
		if self.starting_number is None or self.starting_number < 0:
			frappe.throw(_("Starting Number must be zero or greater."))

		if not self.number_of_leaves or self.number_of_leaves < 1:
			frappe.throw(_("Number of Leaves must be at least 1."))

		max_leaves = frappe.db.get_single_value("Cheque Settings", "max_leaves_per_book") or 500
		if self.number_of_leaves > max_leaves:
			frappe.throw(
				_(
					"A cheque book cannot have more than {0} leaves. Raise the limit in Cheque Settings if this is genuine."
				).format(frappe.bold(max_leaves))
			)

		self.ending_number = self.starting_number + self.number_of_leaves - 1

	def validate_bank_account(self):
		is_company_account, disabled, company = frappe.db.get_value(
			"Bank Account", self.bank_account, ["is_company_account", "disabled", "company"]
		)

		if not is_company_account:
			frappe.throw(
				_(
					"{0} is not a company bank account. Cheque books can only be issued against your own accounts."
				).format(frappe.bold(self.bank_account))
			)

		if disabled:
			frappe.throw(_("Bank Account {0} is disabled.").format(frappe.bold(self.bank_account)))

		if company and company != self.company:
			frappe.throw(
				_("Bank Account {0} belongs to company {1}, not {2}.").format(
					frappe.bold(self.bank_account), frappe.bold(company), frappe.bold(self.company)
				)
			)

	def set_previews(self):
		self.first_cheque_no = compose_cheque_no(
			self.prefix, self.starting_number, self.padding_length, self.suffix
		)
		self.last_cheque_no = compose_cheque_no(
			self.prefix, self.ending_number, self.padding_length, self.suffix
		)

	def before_submit(self):
		self.validate_no_overlap()

	def validate_no_overlap(self):
		"""
		Refuse a book whose numbers collide with leaves already on this account.

		This is the real-world failure: somebody re-enters a book that was already
		recorded, and you end up with two leaves claiming to be cheque 000101.
		"""
		conflict = frappe.db.sql(
			"""
			select leaf.cheque_book, leaf.cheque_no
			from `tabCheque Leaf` leaf
			where leaf.bank_account = %(bank_account)s
				and leaf.cheque_number between %(start)s and %(end)s
				and leaf.cheque_book != %(name)s
			limit 1
			""",
			{
				"bank_account": self.bank_account,
				"start": self.starting_number,
				"end": self.ending_number,
				"name": self.name,
			},
			as_dict=True,
		)

		if conflict:
			frappe.throw(
				_(
					"Cheque numbers {0} to {1} on {2} overlap cheque {3}, which already belongs to {4}."
				).format(
					frappe.bold(self.first_cheque_no),
					frappe.bold(self.last_cheque_no),
					frappe.bold(self.bank_account),
					frappe.bold(conflict[0].cheque_no),
					frappe.bold(conflict[0].cheque_book),
				),
				title=_("Overlapping Cheque Book"),
			)

	def on_submit(self):
		self.db_set("status", "Active")
		self.enqueue_or_generate_leaves()

	def enqueue_or_generate_leaves(self):
		threshold = frappe.db.get_single_value("Cheque Settings", "background_generation_threshold") or 200

		if self.number_of_leaves <= threshold:
			generate_leaves(self.name)
			return

		self.db_set("generation_status", "Queued")
		frappe.enqueue(
			"erpcore.erp_core.doctype.cheque_book.cheque_book.generate_leaves",
			queue="long",
			timeout=1800,
			cheque_book=self.name,
			enqueue_after_commit=True,
		)
		frappe.msgprint(
			_("Generating {0} cheque leaves in the background.").format(self.number_of_leaves),
			indicator="blue",
			alert=True,
		)

	def on_cancel(self):
		used = frappe.db.count("Cheque Leaf", {"cheque_book": self.name, "status": ["!=", LEAF_UNUSED]})
		if used:
			frappe.throw(
				_(
					"Cannot cancel: {0} leaves in this book have already been used, reserved or voided."
				).format(frappe.bold(used)),
				title=_("Cheque Book In Use"),
			)

		frappe.db.delete("Cheque Leaf", {"cheque_book": self.name})
		self.db_set({"status": "Cancelled", "leaves_generated": 0, "generation_status": "Pending"})

	@frappe.whitelist()
	def refresh_counts(self):
		"""Recompute the utilisation stats from the leaves themselves."""
		self.check_permission("read")
		update_counts(self.name)
		return frappe.db.get_value(
			"Cheque Book",
			self.name,
			["unused_count", "issued_count", "cleared_count", "void_count", "status"],
			as_dict=True,
		)


def update_counts(cheque_book):
	"""Roll leaf statuses up onto the book, and close it when nothing is left."""
	rows = frappe.db.sql(
		"""select status, count(*) as count from `tabCheque Leaf`
		where cheque_book = %s group by status""",
		cheque_book,
		as_dict=True,
	)
	counts = {row.status: row.count for row in rows}

	unused = counts.get(LEAF_UNUSED, 0)
	updates = {
		"unused_count": unused,
		"issued_count": counts.get(LEAF_ISSUED, 0),
		"cleared_count": counts.get(LEAF_CLEARED, 0),
		"void_count": counts.get(LEAF_VOID, 0),
	}

	status = frappe.db.get_value("Cheque Book", cheque_book, "status")
	if status in ("Active", "Exhausted") and sum(counts.values()):
		updates["status"] = "Exhausted" if not unused else "Active"

	frappe.db.set_value("Cheque Book", cheque_book, updates, update_modified=False)


def generate_leaves(cheque_book):
	"""Create one Cheque Leaf per number in the book's range.

	Resumable: it starts from whatever has already been generated, so a retry
	after a worker timeout tops the book up rather than duplicating it.

	Deliberately a plain insert loop rather than ``frappe.db.bulk_insert`` --
	bulk insert skips autoname, the creation/owner defaults and the search index,
	so we would be reimplementing four things to save about a second.
	"""
	book = frappe.get_doc("Cheque Book", cheque_book)
	book.db_set("generation_status", "In Progress")

	try:
		highest = frappe.db.sql(
			"""select max(cheque_number) from `tabCheque Leaf` where cheque_book = %s""",
			cheque_book,
		)[0][0]

		start = (highest + 1) if highest is not None else book.starting_number
		total = book.ending_number - start + 1

		if total <= 0:
			book.db_set({"generation_status": "Completed", "leaves_generated": book.number_of_leaves})
			update_counts(cheque_book)
			return

		for index, number in enumerate(range(start, book.ending_number + 1), start=1):
			frappe.get_doc(
				{
					"doctype": "Cheque Leaf",
					"cheque_book": book.name,
					"cheque_no": compose_cheque_no(book.prefix, number, book.padding_length, book.suffix),
					"cheque_number": number,
					"bank_account": book.bank_account,
					"bank": book.bank,
					"company": book.company,
					"status": LEAF_UNUSED,
				}
			).insert(ignore_permissions=True)

			if index % 50 == 0:
				frappe.publish_progress(
					index * 100 / total,
					title=_("Generating Cheque Leaves"),
					doctype="Cheque Book",
					docname=cheque_book,
				)

		generated = frappe.db.count("Cheque Leaf", {"cheque_book": cheque_book})
		book.db_set({"generation_status": "Completed", "leaves_generated": generated})
		update_counts(cheque_book)

	except Exception:
		book.db_set("generation_status", "Failed")
		frappe.log_error(title=f"erpcore: cheque leaf generation failed for {cheque_book}")
		raise
