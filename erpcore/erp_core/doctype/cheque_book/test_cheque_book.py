# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe

from erpcore.erp_core.cheque_constants import (
	LEAF_ISSUED,
	LEAF_RESERVED,
	LEAF_UNUSED,
)
from erpcore.erp_core.doctype.cheque_book.cheque_book import (
	compose_cheque_no,
	generate_leaves,
	update_counts,
)
from erpcore.tests.utils import (
	OTHER_BANK_ACCOUNT,
	OTHER_COMPANY,
	ERPCoreTestCase,
	get_leaves,
	make_bank_account,
	make_cheque_book,
)


class IntegrationTestChequeBook(ERPCoreTestCase):
	def test_compose_cheque_no(self):
		self.assertEqual(compose_cheque_no("CHQ", 101, 6, None), "CHQ000101")
		self.assertEqual(compose_cheque_no(None, 7, 4, "-A"), "0007-A")
		self.assertEqual(compose_cheque_no(None, 7, 0, None), "7")

	def test_defaults_and_previews(self):
		book = make_cheque_book(padding_length=0, starting_number=101, number_of_leaves=5, submit=False)

		# padding_length falls back to Cheque Settings.default_padding_length
		self.assertEqual(book.padding_length, 6)
		self.assertEqual(book.ending_number, 105)
		self.assertEqual(book.first_cheque_no, "CHQ000101")
		self.assertEqual(book.last_cheque_no, "CHQ000105")

	def test_number_of_leaves_must_be_positive(self):
		self.assertRaises(frappe.ValidationError, make_cheque_book, number_of_leaves=0, submit=False)

	def test_number_of_leaves_capped_by_settings(self):
		with self.change_settings("Cheque Settings", max_leaves_per_book=3):
			self.assertRaises(frappe.ValidationError, make_cheque_book, number_of_leaves=5, submit=False)

	def test_rejects_non_company_bank_account(self):
		personal = make_bank_account("_Test Personal", "_Test Bank EUR - _TC", is_company_account=0)
		self.assertRaises(frappe.ValidationError, make_cheque_book, bank_account=personal, submit=False)

	def test_rejects_bank_account_of_another_company(self):
		self.assertRaises(frappe.ValidationError, make_cheque_book, company=OTHER_COMPANY, submit=False)

	def test_submit_generates_every_leaf(self):
		book = make_cheque_book(starting_number=101, number_of_leaves=5)

		self.assertEqual(book.status, "Active")
		self.assertEqual(book.generation_status, "Completed")
		self.assertEqual(book.leaves_generated, 5)

		leaves = get_leaves(book.name)
		self.assertEqual(len(leaves), 5)
		self.assertEqual([row.cheque_number for row in leaves], [101, 102, 103, 104, 105])
		self.assertEqual(leaves[0].cheque_no, "CHQ000101")
		self.assertTrue(all(row.status == LEAF_UNUSED for row in leaves))

	def test_leaf_name_is_book_and_cheque_no(self):
		book = make_cheque_book(number_of_leaves=1)
		self.assertEqual(get_leaves(book.name)[0].name, f"{book.name}-CHQ000101")

	def test_large_book_is_generated_in_background(self):
		with self.change_settings("Cheque Settings", background_generation_threshold=1):
			book = make_cheque_book(number_of_leaves=5)

		self.assertEqual(book.generation_status, "Queued")
		# the job is enqueued after commit, so nothing is generated inline
		self.assertEqual(get_leaves(book.name), [])

	def test_overlapping_book_on_same_account_is_rejected(self):
		make_cheque_book(starting_number=101, number_of_leaves=5)
		overlapping = make_cheque_book(starting_number=103, number_of_leaves=5, submit=False)

		self.assertRaises(frappe.ValidationError, overlapping.submit)

	def test_same_numbers_on_another_account_are_allowed(self):
		make_cheque_book(starting_number=101, number_of_leaves=5)
		other = make_cheque_book(bank_account=OTHER_BANK_ACCOUNT, starting_number=101, number_of_leaves=5)

		self.assertEqual(other.status, "Active")

	def test_cancel_is_blocked_once_a_leaf_is_used(self):
		book = make_cheque_book(number_of_leaves=3)
		frappe.db.set_value("Cheque Leaf", get_leaves(book.name)[0].name, "status", LEAF_RESERVED)

		self.assertRaises(frappe.ValidationError, book.cancel)

	def test_cancel_removes_unused_leaves(self):
		book = make_cheque_book(number_of_leaves=3)
		book.cancel()
		book.reload()

		self.assertEqual(book.status, "Cancelled")
		self.assertEqual(book.leaves_generated, 0)
		self.assertEqual(get_leaves(book.name), [])

	def test_generation_resumes_without_duplicating(self):
		book = make_cheque_book(number_of_leaves=5)
		frappe.delete_doc("Cheque Leaf", get_leaves(book.name)[-1].name)
		self.assertEqual(len(get_leaves(book.name)), 4)

		generate_leaves(book.name)

		leaves = get_leaves(book.name)
		self.assertEqual(len(leaves), 5)
		self.assertEqual([row.cheque_number for row in leaves], [101, 102, 103, 104, 105])

	def test_update_counts_rolls_leaf_statuses_up(self):
		book = make_cheque_book(number_of_leaves=3)
		frappe.db.set_value("Cheque Leaf", get_leaves(book.name)[0].name, "status", LEAF_ISSUED)

		update_counts(book.name)
		book.reload()

		self.assertEqual(book.unused_count, 2)
		self.assertEqual(book.issued_count, 1)
		self.assertEqual(book.status, "Active")

	def test_book_is_exhausted_when_no_leaf_is_left(self):
		book = make_cheque_book(number_of_leaves=2)
		for row in get_leaves(book.name):
			frappe.db.set_value("Cheque Leaf", row.name, "status", LEAF_ISSUED)

		update_counts(book.name)
		book.reload()

		self.assertEqual(book.unused_count, 0)
		self.assertEqual(book.status, "Exhausted")

	def test_refresh_counts_returns_current_stats(self):
		book = make_cheque_book(number_of_leaves=3)
		stats = book.refresh_counts()

		self.assertEqual(stats.unused_count, 3)
		self.assertEqual(stats.status, "Active")
