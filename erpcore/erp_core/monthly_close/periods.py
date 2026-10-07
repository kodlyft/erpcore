# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Calendar-month arithmetic for closes.

A close covers one company and one calendar month, with inclusive bounds. Dates
are plain `date` values in the site's calendar, so there is no timezone maths
to get wrong: a posting dated the 31st belongs to that month whatever the clock
says.
"""

from datetime import date

import frappe
from frappe import _
from frappe.utils import add_months, get_first_day, get_last_day, getdate, nowdate


def month_bounds(value) -> tuple[date, date]:
	day = getdate(value)
	return get_first_day(day), get_last_day(day)


def month_label(value) -> str:
	return getdate(value).strftime("%Y-%m")


def previous_month_start(value) -> date:
	return get_first_day(add_months(getdate(value), -1))


def next_month_start(value) -> date:
	return get_first_day(add_months(getdate(value), 1))


def validate_month(start, end) -> None:
	"""Reject anything that is not exactly one whole calendar month."""
	start, end = getdate(start), getdate(end)
	first, last = month_bounds(start)

	if start != first:
		frappe.throw(_("A close must start on the first day of a month, not {0}.").format(start))

	if end != last:
		frappe.throw(
			_("A close must end on the last day of the same month ({0}), not {1}.").format(last, end)
		)


def month_has_ended(end, today=None) -> bool:
	return getdate(end) < getdate(today or nowdate())


def assert_month_ended(end) -> None:
	"""Planning a future month is fine. Locking one that is still running is not."""
	if not month_has_ended(end):
		frappe.throw(
			_(
				"The month ending {0} has not finished yet. It can be prepared now but hard-closed only after it ends."
			).format(frappe.bold(frappe.format(getdate(end), "Date"))),
			title=_("Month Not Over"),
		)


def fiscal_year_for(company: str, value) -> str:
	"""Name of the company's fiscal year containing the date. Works for any year start."""
	from erpnext.accounts.utils import get_fiscal_year

	return get_fiscal_year(getdate(value), company=company, as_dict=True).name


def fiscal_year_start(fiscal_year: str) -> date:
	return getdate(frappe.get_cached_value("Fiscal Year", fiscal_year, "year_start_date"))
