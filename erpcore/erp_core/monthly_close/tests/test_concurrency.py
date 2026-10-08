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
RACE_REMARK = "erpcore monthly close race"


def race_je(posting_date, amount: float):
	"""A real bank-to-cash journal, submitted through the normal voucher path."""
	cost_center = frappe.get_cached_value("Company", TEST_COMPANY, "cost_center")
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"company": TEST_COMPANY,
			"posting_date": posting_date,
			"user_remark": RACE_REMARK,
			"accounts": [
				{
					"account": "_Test Bank - _TC",
					"debit_in_account_currency": amount,
					"cost_center": cost_center,
				},
				{
					"account": "_Test Cash - _TC",
					"credit_in_account_currency": amount,
					"cost_center": cost_center,
				},
			],
		}
	)
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name


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
			for doctype in ("Monthly Close Event", "Monthly Close Task"):
				frappe.db.delete(doctype, {"monthly_close": name})
			frappe.db.delete("Monthly Close", {"name": name})
		for je in frappe.get_all("Journal Entry", filters={"user_remark": RACE_REMARK}, pluck="name"):
			frappe.db.delete("GL Entry", {"voucher_no": je})
			frappe.db.delete("Journal Entry Account", {"parent": je})
			frappe.db.delete("Journal Entry", {"name": je})
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

		threads = [in_thread(create), in_thread(create)]
		for thread in [pair[0] for pair in threads]:
			thread.join(60)

		results = [pair[1] for pair in threads]
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

	def test_real_journal_inflight_is_seen_by_close(self):
		"""A JE submitting into the month holds the gate; the close waits and then sees its GL rows."""
		name = self.make_committed_close()
		holding = threading.Event()
		posting_date = add_days(self.start, 4)

		def post():
			voucher = race_je(posting_date, 41)
			holding.set()
			time.sleep(2)  # the voucher's transaction is still open, holding the shared gate lock
			return voucher

		thread, result = in_thread(post)
		self.assertTrue(holding.wait(60), result)

		txn.begin("unused")
		started = time.monotonic()
		acquire_close_gate(name)
		waited = time.monotonic() - started
		from erpcore.erp_core.monthly_close import fingerprint

		close = frappe.get_doc("Monthly Close", name)
		components_after_lock = fingerprint.compute(close)[1]
		gl = frappe.db.sql(
			"""select sum(gle.debit), count(*) from `tabGL Entry` gle
			inner join `tabJournal Entry` je on je.name = gle.voucher_no
			where je.user_remark = %s and gle.is_cancelled = 0""",
			RACE_REMARK,
		)[0]
		frappe.db.rollback()
		thread.join(60)

		self.assertNotIn("error", result, result)
		self.assertGreaterEqual(waited, 1.5, "the close must wait for the in-flight voucher")
		self.assertEqual((float(gl[0] or 0), gl[1]), (41.0, 2), "the close must see the committed GL rows")
		self.assertTrue(components_after_lock["gl"])

	def test_real_journal_after_close_lock_is_refused(self):
		name = self.make_committed_close()
		txn.begin("unused")
		acquire_close_gate(name)
		frappe.db.sql("update `tabMonthly Close` set state = 'Closing' where name = %s", name)

		thread, result = in_thread(race_je, add_days(self.start, 6), 17)
		time.sleep(2)
		self.assertTrue(thread.is_alive(), "the voucher must wait while the close holds the gate")
		frappe.db.commit()  # nosemgrep
		thread.join(60)

		self.assertIsInstance(result.get("error"), ClosedMonthError, result)
		self.assertFalse(
			frappe.db.sql(
				"""select gle.name from `tabGL Entry` gle
				inner join `tabJournal Entry` je on je.name = gle.voucher_no
				where je.user_remark = %s""",
				RACE_REMARK,
			),
			"no GL row of the refused voucher may survive",
		)

	def test_task_edit_waits_for_review_and_is_refused(self):
		name = self.make_committed_close()
		frappe.set_user(PREPARER)
		lifecycle.start_close(name)
		frappe.set_user("Administrator")
		frappe.db.commit()  # nosemgrep
		task_name = frappe.db.get_value(
			"Monthly Close Task", {"monthly_close": name, "task_key": "prep"}, "name"
		)

		# Review takes the close lock and moves the state, not yet committed.
		txn.begin("unused")
		acquire_close_gate(name)
		frappe.db.sql("update `tabMonthly Close` set state = 'Ready for Review' where name = %s", name)

		def edit():
			frappe.set_user(PREPARER)
			task = frappe.get_doc("Monthly Close Task", task_name)
			task.status = "Done"
			task.save()

		thread, result = in_thread(edit)
		time.sleep(2)
		self.assertTrue(thread.is_alive(), "the task edit must wait for the review transaction")
		frappe.db.commit()  # nosemgrep
		thread.join(60)

		self.assertIsInstance(result.get("error"), frappe.ValidationError, result)
		self.assertEqual(frappe.db.get_value("Monthly Close Task", task_name, "status"), "Open")

	def test_request_read_view_is_refreshed_before_lock(self):
		"""A request that read before taking the close lock must not act on that stale view."""
		name = self.make_committed_close()
		frappe.set_user(PREPARER)
		lifecycle.start_close(name)
		frappe.set_user("Administrator")
		frappe.db.commit()  # nosemgrep
		task_name = frappe.db.get_value(
			"Monthly Close Task", {"monthly_close": name, "task_key": "prep"}, "name"
		)

		def status():
			return frappe.db.sql("select status from `tabMonthly Close Task` where name = %s", task_name)[0][
				0
			]

		txn.begin("unused")
		self.assertEqual(status(), "Open")  # this plain read opens the request's read view

		def complete():
			frappe.db.sql("update `tabMonthly Close Task` set status = 'Done' where name = %s", task_name)
			frappe.db.sql("update `tabMonthly Close` set revision = revision + 1 where name = %s", name)

		thread, result = in_thread(complete)
		thread.join(60)
		self.assertNotIn("error", result, result)
		self.assertEqual(status(), "Open", "REPEATABLE READ: the old view does not see the commit")

		close = lifecycle.lock_close(name)
		self.assertEqual(close.revision, 2, "the close row comes from a locking read")
		self.assertEqual(status(), "Done", "after lock_close, plain reads see every commit")
		frappe.db.rollback()
