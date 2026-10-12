# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""The earlier-month posting guard and the hard-close sequence rule under real concurrency.

Each scenario runs two independent MariaDB connections, in both operation
orders. Rows are committed for real, in months no other test touches, and
removed afterwards.

* Posting dated before a month (pre-cutover) versus that month's hard close.
* Reopening an earlier month versus hard-closing a later one.
"""

import threading
import time

import frappe
from frappe.utils import add_days, add_months

from erpcore.erp_core.monthly_close import closing, lifecycle, reopen, txn
from erpcore.erp_core.monthly_close.posting_guard import ClosedMonthError, acquire_close_gate
from erpcore.erp_core.monthly_close.tests.test_concurrency import in_thread
from erpcore.erp_core.monthly_close.tests.utils import PREPARER, MonthlyCloseTestCase, month, set_cutover
from erpcore.tests.utils import TEST_COMPANY

EARLIER_OFFSET = 41
LATER_OFFSET = 40
REMARK = "erpcore earlier-month race"


def race_je(posting_date, amount: float):
	cost_center = frappe.get_cached_value("Company", TEST_COMPANY, "cost_center")
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"company": TEST_COMPANY,
			"posting_date": posting_date,
			"user_remark": REMARK,
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


def race_gl_rows():
	return frappe.db.sql(
		"""select count(*), coalesce(sum(gle.debit), 0) from `tabGL Entry` gle
		inner join `tabJournal Entry` je on je.name = gle.voucher_no
		where je.user_remark = %s and gle.is_cancelled = 0""",
		REMARK,
	)[0]


class TestEarlierMonthRaces(MonthlyCloseTestCase):
	def setUp(self):
		super().setUp()
		frappe.flags.erpcore_real_transactions = True
		self.earlier = month(EARLIER_OFFSET)
		self.later = month(LATER_OFFSET)
		self.cleanup()

	def tearDown(self):
		self.cleanup()
		frappe.flags.erpcore_real_transactions = False
		super().tearDown()

	def cleanup(self):
		frappe.db.rollback()
		for name in frappe.get_all(
			"Monthly Close",
			filters={"company": TEST_COMPANY, "period_start": ["in", [self.earlier, self.later]]},
			pluck="name",
		):
			for doctype in ("Monthly Close Event", "Monthly Close Task"):
				frappe.db.delete(doctype, {"monthly_close": name})
			frappe.db.delete("Monthly Close", {"name": name})
		for je in frappe.get_all("Journal Entry", filters={"user_remark": REMARK}, pluck="name"):
			frappe.db.delete("GL Entry", {"voucher_no": je})
			frappe.db.delete("Journal Entry Account", {"parent": je})
			frappe.db.delete("Journal Entry", {"name": je})
		set_cutover(TEST_COMPANY, month(36))
		frappe.db.commit()  # nosemgrep

	def committed_close(self, start, state: str) -> str:
		frappe.set_user(PREPARER)
		name = lifecycle.create_close(TEST_COMPANY, start)
		frappe.set_user("Administrator")
		frappe.db.sql("update `tabMonthly Close` set state = %s where name = %s", (state, name))
		frappe.db.commit()  # nosemgrep
		return name

	# ---------------------------------------------------------------- pre-cutover posting vs hard close

	def test_inflight_pre_cutover_posting_is_seen_by_hard_close(self):
		"""Posting first: the later month's hard close waits for it and then includes it."""
		set_cutover(TEST_COMPANY, self.later)
		later = self.committed_close(self.later, "Approved")
		holding = threading.Event()

		def post():
			voucher = race_je(add_days(self.earlier, 3), 61)
			holding.set()
			time.sleep(2)
			return voucher

		thread, result = in_thread(post)
		self.assertTrue(holding.wait(60), result)

		txn.begin("unused")
		started = time.monotonic()
		acquire_close_gate(later)  # what the hard close lock transaction does first
		waited = time.monotonic() - started
		seen = race_gl_rows()
		frappe.db.rollback()
		thread.join(60)

		self.assertNotIn("error", result, result)
		self.assertGreaterEqual(waited, 1.5, "the hard close must wait for the in-flight earlier posting")
		self.assertEqual((seen[0], float(seen[1])), (2, 61.0), "and then see its GL rows")

	def test_pre_cutover_posting_after_hard_close_lock_is_refused(self):
		"""Hard close first: the posting waits, then sees the locked month and is refused."""
		set_cutover(TEST_COMPANY, self.later)
		later = self.committed_close(self.later, "Approved")

		txn.begin("unused")
		acquire_close_gate(later)
		frappe.db.sql("update `tabMonthly Close` set state = 'Closing' where name = %s", later)

		thread, result = in_thread(race_je, add_days(self.earlier, 4), 19)
		time.sleep(2)
		self.assertTrue(thread.is_alive(), "the earlier posting must wait for the hard close")
		frappe.db.commit()  # nosemgrep
		thread.join(60)

		self.assertIsInstance(result.get("error"), ClosedMonthError, result)
		self.assertEqual(race_gl_rows()[0], 0, "no GL row of the refused posting may survive")

	# ---------------------------------------------------------------- reopen vs hard close

	def test_reopen_committed_first_blocks_later_hard_close(self):
		set_cutover(TEST_COMPANY, self.earlier)
		earlier = self.committed_close(self.earlier, "Closed")
		later = self.committed_close(self.later, "Approved")

		def do_reopen():
			close = lifecycle.lock_close(earlier)
			reopen.reopen(close, stub_request(), False)

		thread, result = in_thread(do_reopen)
		thread.join(60)
		self.assertNotIn("error", result, result)

		txn.begin("unused")
		close = frappe.get_doc("Monthly Close", later, for_update=True)
		problems = closing.sequence_problems(close)
		frappe.db.rollback()
		self.assertTrue(any(add_months(self.later, -1).strftime("%Y-%m") in p for p in problems), problems)

	def test_hard_close_committed_while_reopen_waits_is_flagged_not_overwritten(self):
		"""Hard close holds the later month; reopen waits for it, then must flag the now-Closed month."""
		set_cutover(TEST_COMPANY, self.earlier)
		earlier = self.committed_close(self.earlier, "Closed")
		later = self.committed_close(self.later, "Approved")

		txn.begin("unused")
		acquire_close_gate(later)
		frappe.db.sql("update `tabMonthly Close` set state = 'Closed' where name = %s", later)

		def do_reopen():
			frappe.db.sql("select state from `tabMonthly Close` where name = %s", later)  # an early read view
			close = lifecycle.lock_close(earlier)
			reopen.reopen(close, stub_request(), False)

		thread, result = in_thread(do_reopen)
		time.sleep(2)
		self.assertTrue(thread.is_alive(), "reopen must wait for the later month's lock")
		frappe.db.commit()  # nosemgrep
		thread.join(60)

		self.assertNotIn("error", result, result)
		state, flagged = frappe.db.get_value("Monthly Close", later, ["state", "revalidation_required"])
		self.assertEqual(state, "Closed", "the hard close must not be overwritten by a stale copy")
		self.assertTrue(flagged, "the later Closed month must be flagged for revalidation")
		self.assertEqual(frappe.db.get_value("Monthly Close", earlier, "state"), "Reopened")


def stub_request():
	return frappe._dict(name="(race test)", reason="Race test", requested_by="Administrator")
