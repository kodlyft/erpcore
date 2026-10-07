# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Adapter between a close and ERPNext's native Accounting Period.

* Accounting Period is not submittable. It is active when `disabled = 0`.
* `validate_overlap` ignores `disabled`, so a disabled period still blocks a new
  overlapping one. Reclosing therefore re-enables the period this close owns
  instead of inserting another.
* `validate_dates` refuses an end date after today, which is also why a month
  can only be hard-closed after it ends.
* `before_insert` fills Closed Documents from the `period_closing_doctypes` hook.
* Enforcement (`validate_accounting_period_on_doc_save` and
  `general_ledger.validate_accounting_period`) skips a period whose
  `exempted_role` the user holds. Owned periods never carry one.

The close never edits, adopts, splits or deletes a period it did not create.
An exact-range external period can be associated by a System Manager; anything
else overlapping the month is a conflict for an administrator to resolve.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import getdate

from erpcore.erp_core.monthly_close.constants import LOCK_SERVICE_FLAG, OWNER_FIELD
from erpcore.erp_core.monthly_close.periods import month_label

NONE = "None"
OWNED = "Owned"
ASSOCIATED = "Associated"
COMPATIBLE = "Compatible"
CONFLICT = "Conflict"

PROTECTED_FIELDS = (
	"period_name",
	"company",
	"start_date",
	"end_date",
	"disabled",
	"exempted_role",
	OWNER_FIELD,
)


@dataclass
class LockAssessment:
	kind: str
	owned: str | None = None
	external: list[str] = field(default_factory=list)
	problems: list[str] = field(default_factory=list)


@contextmanager
def lock_service():
	"""Mark writes to owned Accounting Periods as coming from this module."""
	previous = frappe.flags.get(LOCK_SERVICE_FLAG)
	frappe.flags[LOCK_SERVICE_FLAG] = True
	try:
		yield
	finally:
		frappe.flags[LOCK_SERVICE_FLAG] = previous


def required_doctypes() -> list[str]:
	return list(dict.fromkeys(frappe.get_hooks("period_closing_doctypes")))


def period_name_for(close) -> str:
	return f"Monthly Close {month_label(close.period_start)}"


def overlapping_periods(company: str, start, end) -> list[frappe._dict]:
	ap = frappe.qb.DocType("Accounting Period")
	return (
		frappe.qb.from_(ap)
		.select(
			ap.name,
			ap.start_date,
			ap.end_date,
			ap.disabled,
			ap.exempted_role,
			ap[OWNER_FIELD].as_("owner_close"),
		)
		.where(ap.company == company)
		.where(ap.start_date <= end)
		.where(ap.end_date >= start)
		.orderby(ap.start_date)
		.run(as_dict=True)
	)


def coverage_problems(period_name: str, close) -> list[str]:
	"""Why this period would not lock the whole month for every closable voucher type."""
	period = frappe.db.get_value(
		"Accounting Period",
		period_name,
		["company", "start_date", "end_date", "exempted_role"],
		as_dict=True,
	)
	if not period:
		return [_("Accounting Period {0} no longer exists.").format(period_name)]

	problems = []
	if period.company != close.company:
		problems.append(_("Company is {0}, expected {1}.").format(period.company, close.company))

	if getdate(period.start_date) != getdate(close.period_start) or getdate(period.end_date) != getdate(
		close.period_end
	):
		problems.append(
			_("Dates are {0} to {1}, expected {2} to {3}.").format(
				period.start_date, period.end_date, close.period_start, close.period_end
			)
		)

	if period.exempted_role:
		problems.append(_("Role {0} is exempted from the lock.").format(period.exempted_role))

	closed = set(
		frappe.get_all(
			"Closed Document",
			filters={"parent": period_name, "parenttype": "Accounting Period", "closed": 1},
			pluck="document_type",
		)
	)
	missing = [doctype for doctype in required_doctypes() if doctype not in closed]
	if missing:
		problems.append(_("Not closed for: {0}.").format(", ".join(missing)))

	return problems


def assess(close) -> LockAssessment:
	periods = overlapping_periods(close.company, close.period_start, close.period_end)
	owned = [p.name for p in periods if p.owner_close == close.name]
	external = [p for p in periods if p.owner_close != close.name]

	if not external:
		return LockAssessment(OWNED if owned else NONE, owned=owned[0] if owned else None)

	names = [p.name for p in external]
	if owned or len(external) > 1:
		return LockAssessment(
			CONFLICT,
			owned=owned[0] if owned else None,
			external=names,
			problems=[_("Several Accounting Periods overlap this month: {0}.").format(", ".join(names))],
		)

	period = external[0]
	exact = getdate(period.start_date) == getdate(close.period_start) and getdate(period.end_date) == getdate(
		close.period_end
	)
	if not exact:
		return LockAssessment(
			CONFLICT,
			external=names,
			problems=[
				_("Accounting Period {0} covers {1} to {2}, which does not match this month.").format(
					period.name, period.start_date, period.end_date
				)
			],
		)

	if period.owner_close:
		return LockAssessment(
			CONFLICT,
			external=names,
			problems=[
				_("Accounting Period {0} belongs to close {1}.").format(period.name, period.owner_close)
			],
		)

	problems = coverage_problems(period.name, close)
	if problems:
		return LockAssessment(CONFLICT, external=names, problems=problems)

	if close.external_accounting_period == period.name:
		if period.disabled:
			return LockAssessment(
				CONFLICT,
				external=names,
				problems=[_("Associated Accounting Period {0} is disabled.").format(period.name)],
			)
		return LockAssessment(ASSOCIATED, external=names)

	return LockAssessment(COMPATIBLE, external=names)


def establish(close) -> tuple[str, bool]:
	"""Make sure the month is natively locked. Returns (period name, owned by this close)."""
	assessment = assess(close)

	if assessment.kind == CONFLICT:
		frappe.throw(
			_("The month cannot be locked until an administrator resolves this: {0}").format(
				" ".join(assessment.problems)
			),
			title=_("Accounting Period Conflict"),
		)

	if assessment.kind == COMPATIBLE:
		frappe.throw(
			_(
				"Accounting Period {0} already locks exactly this month. A System Manager must associate it with this close before hard closing, or remove it."
			).format(frappe.bold(assessment.external[0])),
			title=_("Existing Accounting Period"),
		)

	if assessment.kind == ASSOCIATED:
		return assessment.external[0], False

	if assessment.kind == OWNED:
		problems = coverage_problems(assessment.owned, close)
		if problems:
			frappe.throw(
				_("Owned Accounting Period {0} was altered and cannot be trusted: {1}").format(
					frappe.bold(assessment.owned), " ".join(problems)
				),
				title=_("Lock Tampered"),
			)

		period = frappe.get_doc("Accounting Period", assessment.owned, for_update=True)
		if period.disabled:
			period.disabled = 0
			with lock_service():
				period.save(ignore_permissions=True)
		return period.name, True

	period = frappe.get_doc(
		{
			"doctype": "Accounting Period",
			"period_name": period_name_for(close),
			"company": close.company,
			"start_date": close.period_start,
			"end_date": close.period_end,
			OWNER_FIELD: close.name,
			"closed_documents": [{"document_type": dt, "closed": 1} for dt in required_doctypes()],
		}
	)
	with lock_service():
		period.insert(ignore_permissions=True)

	problems = coverage_problems(period.name, close)
	if problems:
		frappe.throw(_("The new Accounting Period does not cover the month: {0}").format(" ".join(problems)))

	return period.name, True


def release(close) -> bool:
	"""Disable the lock this close owns. External periods are left exactly as they are."""
	if not (close.lock_owned and close.accounting_period):
		return False

	if not frappe.db.exists("Accounting Period", close.accounting_period):
		return False

	period = frappe.get_doc("Accounting Period", close.accounting_period, for_update=True)
	if period.get(OWNER_FIELD) != close.name:
		return False

	if not period.disabled:
		period.disabled = 1
		with lock_service():
			period.save(ignore_permissions=True)
	return True


def lock_health(close) -> dict:
	"""Is the native lock that a Closing/Closed close depends on still in force?"""
	from erpcore.erp_core.monthly_close.constants import LOCKED_STATES

	if close.state not in LOCKED_STATES:
		return {"status": "Not Locked", "problems": []}

	if not close.accounting_period:
		return {"status": "Failed", "problems": [_("No Accounting Period is linked to this close.")]}

	disabled = frappe.db.get_value("Accounting Period", close.accounting_period, "disabled")
	problems = coverage_problems(close.accounting_period, close)
	if disabled:
		problems.insert(0, _("Accounting Period {0} is disabled.").format(close.accounting_period))

	if close.lock_owned:
		owner = frappe.db.get_value("Accounting Period", close.accounting_period, OWNER_FIELD)
		if owner and owner != close.name:
			problems.append(_("Accounting Period ownership was changed to {0}.").format(owner))

	return {"status": "Failed" if problems else "Healthy", "problems": problems}


def protect_owned_period(doc, method=None):
	"""Accounting Period `validate` hook: owned periods change only through this module."""
	if frappe.flags.get(LOCK_SERVICE_FLAG):
		return

	before = None if doc.is_new() else doc.get_doc_before_save()
	owner = before.get(OWNER_FIELD) if before else None

	if not owner:
		if doc.get(OWNER_FIELD):
			frappe.throw(
				_("Only the Monthly Close service can mark an Accounting Period as owned by a close."),
				frappe.PermissionError,
			)
		return

	changed = [f for f in PROTECTED_FIELDS if str(before.get(f) or "") != str(doc.get(f) or "")]

	def coverage(d):
		return sorted((row.document_type, int(row.closed or 0)) for row in d.closed_documents)

	if changed or coverage(before) != coverage(doc):
		frappe.throw(
			_(
				"Accounting Period {0} is the lock for Monthly Close {1}. Reopen the close through its Reopen Request instead of editing the period."
			).format(frappe.bold(doc.name), frappe.bold(owner)),
			frappe.PermissionError,
			title=_("Protected Accounting Period"),
		)


def protect_owned_period_delete(doc, method=None):
	if frappe.flags.get(LOCK_SERVICE_FLAG) or not doc.get(OWNER_FIELD):
		return

	frappe.throw(
		_("Accounting Period {0} is the lock for Monthly Close {1} and cannot be deleted.").format(
			frappe.bold(doc.name), frappe.bold(doc.get(OWNER_FIELD))
		),
		frappe.PermissionError,
		title=_("Protected Accounting Period"),
	)
