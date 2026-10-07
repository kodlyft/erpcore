# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Versioned registry of allowlisted close checks.

Policies can only switch registered checks on or off and set their severity and
tolerance. They never carry SQL or Python. Other apps add checks through the
`erpcore_monthly_close_checks` hook, which lists dotted paths to modules that
call `register_check` on import.

A check function receives a `CheckContext` and returns a `Finding`. It reports
what it saw; the runner turns a finding into Warning or Blocker from the policy.
A check that cannot evaluate returns NOT_APPLICABLE (with a reason) or raises,
which is recorded as Error. Neither is ever a Passed.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import frappe
from frappe.utils import flt

from erpcore.erp_core.monthly_close.constants import BLOCKER, ERROR, NOT_APPLICABLE, PASSED, WARNING

FINDING = "Finding"


@dataclass
class CheckContext:
	company: str
	period_start: date
	period_end: date
	fiscal_year: str
	close_name: str
	revision: int
	currency: str
	precision: int
	severity: str
	tolerance: float
	sample_limit: int
	policy: object


@dataclass
class Finding:
	outcome: str = PASSED  # PASSED, FINDING or NOT_APPLICABLE
	message: str = ""
	count: int = 0
	amount: float = 0.0
	samples: list[dict] = field(default_factory=list)
	route: str | None = None
	minimum_severity: str | None = None  # e.g. an unbalanced trial balance is always a Blocker
	tolerance_applies: bool = True  # False when the finding is missing evidence, not an amount


@dataclass
class CheckDefinition:
	check_id: str
	version: int
	label: str
	default_severity: str
	function: Callable[[CheckContext], Finding]
	description: str = ""
	uses_tolerance: bool = False


_REGISTRY: dict[str, CheckDefinition] = {}
_LOADED = False


def register_check(
	check_id: str,
	version: int,
	label: str,
	default_severity: str = WARNING,
	description: str = "",
	uses_tolerance: bool = False,
):
	def decorator(function):
		_REGISTRY[check_id] = CheckDefinition(
			check_id=check_id,
			version=version,
			label=label,
			default_severity=default_severity,
			function=function,
			description=description,
			uses_tolerance=uses_tolerance,
		)
		return function

	return decorator


def _load():
	global _LOADED
	if _LOADED:
		return

	from erpcore.erp_core.monthly_close.checks import assets, ledger, stock, subledgers

	for module in frappe.get_hooks("erpcore_monthly_close_checks"):
		frappe.get_module(module)

	_LOADED = True


def get_checks() -> dict[str, CheckDefinition]:
	_load()
	return dict(_REGISTRY)


def get_check(check_id: str) -> CheckDefinition | None:
	return get_checks().get(check_id)


def resolve_status(finding: Finding, severity: str, tolerance: float, uses_tolerance: bool) -> str:
	"""Turn a raw finding into the status recorded on the result row."""
	if finding.outcome == NOT_APPLICABLE:
		return NOT_APPLICABLE

	if finding.outcome == PASSED:
		return PASSED

	if (
		uses_tolerance
		and finding.tolerance_applies
		and not finding.minimum_severity
		and abs(flt(finding.amount)) <= abs(flt(tolerance))
	):
		return PASSED

	if finding.minimum_severity == BLOCKER or severity == BLOCKER:
		return BLOCKER

	return WARNING


def bounded(rows, limit: int) -> list[dict]:
	"""Keep samples small: full detail belongs in the drill-down report, not in JSON on the result."""
	return [dict(row) for row in list(rows)[: max(int(limit or 0), 0)]]


__all__ = [
	"BLOCKER",
	"ERROR",
	"FINDING",
	"NOT_APPLICABLE",
	"PASSED",
	"WARNING",
	"CheckContext",
	"CheckDefinition",
	"Finding",
	"bounded",
	"get_check",
	"get_checks",
	"register_check",
	"resolve_status",
]
