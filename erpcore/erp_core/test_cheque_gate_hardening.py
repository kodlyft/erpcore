# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Cheque and gate-pass hardening: atomic reservation, concurrent returns, lookup permissions,
company-scoped notifications, queued leaf generation and preserved custom workflows."""

import threading
import time
from unittest.mock import patch

import frappe
from erpnext.accounts.doctype.payment_entry.test_payment_entry import create_payment_entry
from frappe.utils import add_days, nowdate

from erpcore.erp_core.cheque_constants import LEAF_RESERVED
from erpcore.erp_core.doctype.cheque_book.cheque_book import generate_leaves
from erpcore.erp_core.doctype.cheque_leaf.cheque_leaf import get_available_leaves
from erpcore.erp_core.tasks import notify_overdue_gate_pass_returns
from erpcore.erp_core.workflows import WORKFLOW_NAME, setup_gate_pass_workflow
from erpcore.tests.utils import (
	OTHER_COMPANY,
	TEST_COMPANY,
	ERPCoreTestCase,
	get_leaves,
	make_cheque_book,
	make_gate_pass,
	make_gate_pass_return,
	make_user,
)

RACE_PREFIX = "RACE"
RACE_REMARK = "erpcore cheque/gate race"


def in_thread(target, *args):
	"""Run `target` on its own connection and commit; collect its result or exception."""
	result = {}
	site = frappe.local.site

	def runner():
		frappe.init(site=site)
		frappe.connect()
		frappe.flags.in_test = True
		try:
			result["value"] = target(*args)
			frappe.db.commit()  # nosemgrep
		except Exception as exc:
			frappe.db.rollback()
			result["error"] = exc
		finally:
			frappe.destroy()

	thread = threading.Thread(target=runner)
	thread.start()
	return thread, result


class TestChequeGateRaces(ERPCoreTestCase):
	"""Real, independently committing connections; everything they create is removed afterwards."""

	def setUp(self):
		self.cleanup()

	def tearDown(self):
		self.cleanup()

	def cleanup(self):
		frappe.db.rollback()
		for payment in frappe.get_all("Payment Entry", filters={"remarks": RACE_REMARK}, pluck="name"):
			for child in ("Payment Entry Reference", "Payment Entry Deduction", "Advance Taxes and Charges"):
				frappe.db.delete(child, {"parent": payment})
			frappe.db.delete("Payment Entry", {"name": payment})
		for book in frappe.get_all("Cheque Book", filters={"prefix": RACE_PREFIX}, pluck="name"):
			frappe.db.delete("Cheque Leaf", {"cheque_book": book})
			frappe.db.delete("Cheque Book", {"name": book})
		for gate_pass in frappe.get_all("Gate Pass", filters={"purpose": RACE_REMARK}, pluck="name"):
			for ret in frappe.get_all(
				"Gate Pass Return", filters={"return_against": gate_pass}, pluck="name"
			):
				frappe.db.delete("Gate Pass Return Item", {"parent": ret})
				frappe.db.delete("Gate Pass Return", {"name": ret})
			frappe.db.delete("Gate Pass Item", {"parent": gate_pass})
			frappe.db.delete("Gate Pass", {"name": gate_pass})
		frappe.db.commit()  # nosemgrep

	def test_two_drafts_cannot_reserve_the_same_leaf(self):
		book = make_cheque_book(prefix=RACE_PREFIX, starting_number=9001, number_of_leaves=2)
		leaf = get_leaves(book.name)[0].name
		frappe.db.commit()  # nosemgrep
		barrier = threading.Barrier(2)

		def draft(delay):
			barrier.wait()
			time.sleep(delay)
			payment = create_payment_entry()
			payment.remarks = RACE_REMARK
			payment.cheque_leaf = leaf
			payment.save()
			time.sleep(1.5)  # keep the transaction (and any lock) open
			return payment.name

		threads = [in_thread(draft, 0), in_thread(draft, 0.3)]
		for thread, _result in threads:
			thread.join(60)
		results = [result for thread, result in threads]

		saved = [r["value"] for r in results if "value" in r]
		self.assertEqual(len(saved), 1, results)
		self.assertIn("reserved", str([r.get("error") for r in results]).lower())
		frappe.db.rollback()
		reserved = frappe.db.get_value("Cheque Leaf", leaf, ["status", "reference_name"], as_dict=True)
		self.assertEqual((reserved.status, reserved.reference_name), (LEAF_RESERVED, saved[0]))
		self.assertEqual(frappe.db.count("Payment Entry", {"cheque_leaf": leaf}), 1)

	def test_concurrent_returns_cannot_exceed_what_went_out(self):
		gate_pass = make_gate_pass(returnable=1, purpose=RACE_REMARK)
		row = gate_pass.items[0].name  # qty 10
		frappe.db.commit()  # nosemgrep
		barrier = threading.Barrier(2)

		def give_back(delay):
			barrier.wait()
			time.sleep(delay)
			doc = make_gate_pass_return(gate_pass.name, {row: 6})
			time.sleep(1.5)
			return doc.name

		threads = [in_thread(give_back, 0), in_thread(give_back, 0.3)]
		for thread, _result in threads:
			thread.join(60)
		results = [result for thread, result in threads]

		self.assertEqual(len([r for r in results if "value" in r]), 1, results)
		frappe.db.rollback()
		returned = frappe.db.sql(
			"""select coalesce(sum(i.returned_qty), 0) from `tabGate Pass Return Item` i
			inner join `tabGate Pass Return` r on r.name = i.parent
			where r.return_against = %s and r.docstatus = 1""",
			gate_pass.name,
		)[0][0]
		self.assertLessEqual(returned, 10)


class TestChequeGateHardening(ERPCoreTestCase):
	def test_lookup_respects_company_user_permissions(self):
		book = make_cheque_book(number_of_leaves=2)
		user = make_user("_test_cheque_other_company@example.com", "_Test", "cheque other", ["Accounts User"])
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": "Company",
				"for_value": OTHER_COMPANY,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=user)

		visible_to_admin = [row[0] for row in get_available_leaves("Cheque Leaf", "", "name", 0, 50, {})]
		self.assertIn(get_leaves(book.name)[0].name, visible_to_admin)
		frappe.set_user(user)
		try:
			visible = [row[0] for row in get_available_leaves("Cheque Leaf", "", "name", 0, 50, {})]
			# Asking for the other company explicitly does not help either.
			asked = get_available_leaves("Cheque Leaf", "", "name", 0, 50, {"company": TEST_COMPANY})
		finally:
			frappe.set_user("Administrator")
		self.assertNotIn(get_leaves(book.name)[0].name, visible)
		self.assertEqual(list(asked), [])

	def test_overdue_digest_is_scoped_per_recipient(self):
		ours = make_gate_pass(
			returnable=1, posting_date=add_days(nowdate(), -10), expected_return_date=add_days(nowdate(), -2)
		)
		frappe.db.set_value("Gate Pass", ours.name, "status", "Overdue")
		restricted = make_user("_test_gate_other_company@example.com", "_Test", "gate other", ["Gate Keeper"])
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": restricted,
				"allow": "Company",
				"for_value": OTHER_COMPANY,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		disabled = make_user("_test_gate_disabled@example.com", "_Test", "gate disabled", ["Gate Keeper"])
		frappe.db.set_value("User", disabled, "enabled", 0)
		frappe.clear_cache()

		settings = frappe.get_doc("Gate Pass Settings")
		settings.notify_roles = []
		settings.append("notify_roles", {"role": "Gate Keeper"})
		settings.save()

		queued_before = set(frappe.get_all("Email Queue", pluck="name"))
		notify_overdue_gate_pass_returns()
		new_mail = [
			frappe.get_doc("Email Queue", name)
			for name in frappe.get_all("Email Queue", pluck="name")
			if name not in queued_before
		]
		recipients = {r.recipient for mail in new_mail for r in mail.recipients}
		self.assertNotIn(restricted, recipients, "a user of another company gets no digest of ours")
		self.assertNotIn(disabled, recipients)
		for mail in new_mail:
			self.assertEqual(len(mail.recipients), 1, "one digest per recipient")

	def test_leaf_generation_is_idempotent_and_skips_cancelled_books(self):
		book = make_cheque_book(number_of_leaves=4)
		generate_leaves(book.name)  # a duplicate or retried job
		numbers = [row.cheque_number for row in get_leaves(book.name)]
		self.assertEqual(len(numbers), len(set(numbers)))
		self.assertEqual(len(numbers), 4)

		pending = make_cheque_book(number_of_leaves=3, starting_number=501)
		frappe.db.set_value("Cheque Book", pending.name, "generation_status", "Queued")
		pending.reload()
		with patch("frappe.utils.background_jobs.is_job_enqueued", return_value=True):
			self.assertRaisesRegex(frappe.ValidationError, "still being generated", pending.cancel)
		# A job that died no longer blocks the cancellation.
		with patch("frappe.utils.background_jobs.is_job_enqueued", return_value=False):
			pending.reload()
			pending.cancel()
		generate_leaves(pending.name)  # a job that runs after the cancellation does nothing
		self.assertEqual(get_leaves(pending.name), [])

	def test_return_must_match_the_source_company_and_items(self):
		gate_pass = make_gate_pass(returnable=1)
		other = make_gate_pass(returnable=1)
		from erpcore.erp_core.doctype.gate_pass_return.gate_pass_return import (
			make_gate_pass_return as map_return,
		)

		# Company is fetched from the source and read-only: a client value cannot stick.
		doc = map_return(gate_pass.name)
		doc.company = OTHER_COMPANY
		doc.insert(ignore_permissions=True)
		self.assertEqual(doc.company, gate_pass.company)

		doc = map_return(gate_pass.name)
		doc.items[0].gate_pass_item = other.items[0].name
		self.assertRaisesRegex(frappe.ValidationError, "not an item of", doc.insert)

	def test_migrate_keeps_a_customised_workflow(self):
		setup_gate_pass_workflow()
		workflow = frappe.get_doc("Workflow", WORKFLOW_NAME)
		workflow.append(
			"transitions",
			{
				"state": "Rejected",
				"action": "Approve",
				"next_state": "Approved",
				"allowed": "Gate Pass Manager",
			},
		)
		workflow.save(ignore_permissions=True)
		customised = len(workflow.transitions)

		setup_gate_pass_workflow()  # what every migrate runs
		self.assertEqual(len(frappe.get_doc("Workflow", WORKFLOW_NAME).transitions), customised)
