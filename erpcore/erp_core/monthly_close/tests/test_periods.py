# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from datetime import date

import frappe
from frappe.utils import add_months, get_first_day, nowdate

from erpcore.erp_core.monthly_close import periods, snapshot
from erpcore.erp_core.monthly_close.tests.utils import MonthlyCloseTestCase
from erpcore.tests.utils import TEST_COMPANY


class TestPeriods(MonthlyCloseTestCase):
	def test_month_bounds_handle_february_and_leap_years(self):
		self.assertEqual(periods.month_bounds("2024-02-10"), (date(2024, 2, 1), date(2024, 2, 29)))
		self.assertEqual(periods.month_bounds("2023-02-10"), (date(2023, 2, 1), date(2023, 2, 28)))
		self.assertEqual(periods.month_bounds("2025-12-31"), (date(2025, 12, 1), date(2025, 12, 31)))

	def test_rejects_partial_and_cross_month_ranges(self):
		periods.validate_month("2024-02-01", "2024-02-29")
		self.assertRaises(frappe.ValidationError, periods.validate_month, "2024-02-02", "2024-02-29")
		self.assertRaises(frappe.ValidationError, periods.validate_month, "2024-02-01", "2024-03-31")
		self.assertRaises(frappe.ValidationError, periods.validate_month, "2023-02-01", "2023-02-27")

	def test_future_and_current_months_cannot_be_hard_closed(self):
		current = get_first_day(nowdate())
		self.assertRaises(
			frappe.ValidationError, periods.assert_month_ended, periods.month_bounds(current)[1]
		)
		next_month = periods.month_bounds(add_months(current, 1))[1]
		self.assertRaises(frappe.ValidationError, periods.assert_month_ended, next_month)
		periods.assert_month_ended(periods.month_bounds(add_months(current, -1))[1])

	def test_non_january_fiscal_year(self):
		# The bootstrap's short fiscal year runs April to December 2011.
		fiscal_year = periods.fiscal_year_for(TEST_COMPANY, "2011-05-15")
		self.assertEqual(periods.fiscal_year_start(fiscal_year), date(2011, 4, 1))

		close = frappe._dict(
			name="MC-TEST",
			company=TEST_COMPANY,
			revision=1,
			period_start=date(2011, 5, 1),
			period_end=date(2011, 5, 31),
			fiscal_year=fiscal_year,
		)
		specs = {(s["report"], s["scope"]): s for s in snapshot.report_specs(close)}
		ytd = specs[("Profit and Loss Statement", "Year to Date")]
		month_only = specs[("Profit and Loss Statement", "Month")]
		self.assertEqual(ytd["filters"]["period_start_date"], date(2011, 4, 1))
		self.assertEqual(month_only["filters"]["period_start_date"], date(2011, 5, 1))
		self.assertEqual(month_only["filters"]["accumulated_values"], 0)
		self.assertEqual(ytd["filters"]["accumulated_values"], 1)
