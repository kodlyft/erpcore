# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Concurrency with real, independent MariaDB connections.

Each worker thread opens its own Frappe connection, so locks and commits behave
as they do between two web workers. These tests commit, so they create their
own close row for a dedicated month and delete it afterwards.
"""

import threading
import time

import frappe
from frappe.utils import add_days

from erpcore.erp_core.monthly_close import lifecycle, txn
from erpcore.erp_core.monthly_close.posting_guard import (
	ClosedMonthError,
	acquire_close_gate,
	acquire_posting_gate,
	guard_ledger_entry,
)
from erpcore.erp_core.monthly_close.tests.utils import (
	PREPARER,
	MonthlyCloseTestCase,
	month,
	set_cutover,
)
from erpcore.tests.utils import TEST_COMPANY

OFFSET = 30  # a month no other test touches
MARKER = "erpcore_mc_concurrency_marker"


def in_thread(target, *args):
	"""Run `target` on its own connection; collect its result or exception."""
	result = {}
	site = frappe.local.site

	def runner():
		frappe.init(site=site)
		frappe.connect()
		frappe.flags.in_test = True
		frappe.flags.erpcore_real_transactions = True
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


class TestConcurrency(MonthlyCloseTestCase):
	def setUp(self):
		super().setUp()
		frappe.flags.erpcore_real_transactions = True
		self.start = month(OFFSET)
		set_cutover(TEST_COMPANY, self.start)
		frappe.db.commit()  # nosemgrep
		self.cleanup()

	def tearDown(self):
		self.cleanup()
		frappe.flags.erpcore_real_transactions = False
		super().tearDown()

	def cleanup(self):
		frappe.db.rollback()
		for name in frappe.get_all(
			"Monthly Close", filters={"company": TEST_COMPANY, "period_start": self.start}, pluck="name"
		):
			frappe.db.delete("Monthly Close Event", {"monthly_close": name})
			frappe.db.delete("Monthly Close", {"name": name})
		frappe.db.set_global(MARKER, None)
		frappe.db.commit()  # nosemgrep

	def make_committed_close(self) -> str:
		frappe.set_user(PREPARER)
		name = lifecycle.create_close(TEST_COMPANY, self.start)
		frappe.set_user("Administrator")
		frappe.db.commit()  # nosemgrep
		return name

	def test_duplicate_simultaneous_creation(self):
		barrier = threading.Barrier(2)

		def create():
			frappe.set_user(PREPARER)
			barrier.wait()
			return lifecycle.create_close(TEST_COMPANY, self.start)

		threads = [in_thread(create) for _ in range(2)]
		for thread, _result in threads:
			thread.join(60)

		results = [r for _t, r in threads]
		created = [r["value"] for r in results if "value" in r]
		errors = [r["error"] for r in results if "error" in r]
		self.assertEqual(len(created), 1, results)
		self.assertEqual(len(errors), 1, results)
		self.assertIsInstance(errors[0], frappe.DuplicateEntryError | frappe.QueryDeadlockError)
		frappe.db.rollback()
		self.assertEqual(
			frappe.db.count("Monthly Close", {"company": TEST_COMPANY, "period_start": self.start}), 1
		)

	def test_close_waits_for_inflight_posting_and_then_sees_it(self):
		name = self.make_committed_close()
		holding = threading.Event()

		def posting():
			# The posting path: shared lock on the gate, then work, then commit.
			acquire_posting_gate(TEST_COMPANY, self.start)
			holding.set()
			time.sleep(2)
			frappe.db.set_global(MARKER, "posted")

		thread, result = in_thread(posting)
		self.assertTrue(holding.wait(30))

		# The close path: a fresh transaction, then FOR UPDATE on the same row.
		txn.begin("unused")
		started = time.monotonic()
		acquire_close_gate(name)
		waited = time.monotonic() - started
		seen = frappe.db.get_global(MARKER)
		frappe.db.rollback()
		thread.join(60)

		self.assertNotIn("error", result, result)
		self.assertGreaterEqual(waited, 1.5, "hard close must wait for the in-flight posting")
		self.assertEqual(seen, "posted", "after the lock, the close must see the committed posting")

	def test_posting_after_close_lock_is_refused(self):
		name = self.make_committed_close()

		# The close holds the gate and moves to Closing, but has not committed yet.
		txn.begin("unused")
		acquire_close_gate(name)
		frappe.db.sql("update `tabMonthly Close` set state = 'Closing' where name = %s", name)

		ledger_row = frappe._dict(
			doctype="GL Entry",
			company=TEST_COMPANY,
			posting_date=add_days(self.start, 3),
			voucher_type="Journal Entry",
			voucher_no="ACC-JV-TEST",
		)
		thread, result = in_thread(guard_ledger_entry, ledger_row)
		time.sleep(2)
		self.assertTrue(thread.is_alive(), "the posting must wait while the close holds the gate")

		frappe.db.commit()  # nosemgrep
		thread.join(60)
		self.assertIsInstance(result.get("error"), ClosedMonthError, result)

	def test_close_versus_close_is_serialised(self):
		name = self.make_committed_close()
		holding = threading.Event()

		def first():
			acquire_close_gate(name)
			holding.set()
			time.sleep(2)
			frappe.db.sql("update `tabMonthly Close` set revision = revision + 1 where name = %s", name)

		thread, result = in_thread(first)
		self.assertTrue(holding.wait(30))
		txn.begin("unused")
		row = acquire_close_gate(name)
		frappe.db.rollback()
		thread.join(60)

		self.assertNotIn("error", result, result)
		self.assertEqual(row.revision, 2, "the second actor must see the first actor's committed change")
