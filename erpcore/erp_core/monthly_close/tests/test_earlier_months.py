# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Backdated GL/PLE/SLE changes in an earlier month never silently change a closed later month.

A posting dated before a locked month changes that month's balances brought
forward. It is allowed only when every later locked month has already been
flagged for revalidation (which reopening an earlier managed month does);
otherwise it is refused. Months are also hard-closed only after every earlier
managed month is Closed and revalidated.
"""

import frappe
from frappe.utils import add_days

from erpcore.erp_core.monthly_close import closing, lifecycle
from erpcore.erp_core.monthly_close.posting_guard import ClosedMonthError
from erpcore.erp_core.monthly_close.tests.test_hard_close import reclose
from erpcore.erp_core.monthly_close.tests.test_posting_paths import party_je
from erpcore.erp_core.monthly_close.tests.utils import (
	MANAGER,
	PREPARER,
	REVIEWER,
	MonthlyCloseTestCase,
	as_user,
	drive_to_approved,
	drive_to_closed,
	make_je,
	month,
	reopen_close,
	run_checks,
)


def reapprove(name: str):
	"""Approve a close again after it was sent back."""
	if frappe.db.get_value("Monthly Close", name, "state") == "Approved":
		return
	run_checks(name)
	with as_user(PREPARER):
		lifecycle.submit_for_review(name)
	with as_user(REVIEWER):
		lifecycle.approve(name)


class TestEarlierMonths(MonthlyCloseTestCase):
	def test_unmanaged_month_before_cutover_cannot_change_closed_month(self):
		name = drive_to_closed(3)  # cutover = month 3, so month 4 is unmanaged and open
		self.assertRaises(ClosedMonthError, make_je, add_days(month(4), 5), 25)
		self.assertRaises(ClosedMonthError, party_je, add_days(month(4), 5), 25)
		self.assertFalse(frappe.db.get_value("Monthly Close", name, "revalidation_required"))

		# Reopening the closed month is the controlled way in: then the earlier posting is allowed.
		reopen_close(name)
		make_je(add_days(month(4), 5), 25)

	def test_posting_into_reopened_month_needs_later_months_flagged(self):
		earlier = drive_to_closed(4)
		later = drive_to_closed(3, cutover=False)
		reopen_close(earlier)
		self.assertTrue(frappe.db.get_value("Monthly Close", later, "revalidation_required"))
		make_je(add_days(month(4), 6), 30)  # later month is flagged: allowed

	def test_cannot_hard_close_past_an_open_or_unrevalidated_earlier_month(self):
		first = drive_to_closed(5)
		second = drive_to_closed(4, cutover=False)
		third = drive_to_approved(3, cutover=False)
		reopen_close(first)  # second is flagged, still Closed; third is sent back to In Progress
		reapprove(third)

		with as_user(MANAGER):
			self.assertRaisesRegex(
				closing.HardCloseRefused, "closed first|revalidated", closing.request_hard_close, third
			)

		reclose(first)
		reapprove(third)
		with as_user(MANAGER):
			# first is Closed again, but second still awaits revalidation.
			self.assertRaisesRegex(closing.HardCloseRefused, "revalidated", closing.request_hard_close, third)
		self.assertEqual(frappe.db.get_value("Monthly Close", second, "state"), "Closed")
		self.assertTrue(frappe.db.get_value("Monthly Close", second, "revalidation_required"))
