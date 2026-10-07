# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Transaction boundaries of the background close jobs.

Workers commit between steps (see closing.py and check_runner.py). Under the
test runner those commits would leak fixtures out of the per-class rollback,
so in tests a step boundary is a savepoint instead. Concurrency tests that need
real, separately visible commits set `frappe.flags.erpcore_real_transactions`
and clean up after themselves.
"""

import frappe


def _simulated() -> bool:
	in_test = getattr(frappe, "in_test", False) or frappe.flags.in_test
	return bool(in_test) and not frappe.flags.get("erpcore_real_transactions")


def begin(name: str) -> None:
	"""Start a step. In production this ends any earlier read view, so the next read sees all commits."""
	if _simulated():
		frappe.db.savepoint(name)
	else:
		frappe.db.commit()  # nosemgrep


def commit() -> None:
	if not _simulated():
		frappe.db.commit()  # nosemgrep


def rollback(name: str) -> None:
	if _simulated():
		frappe.db.rollback(save_point=name)
	else:
		frappe.db.rollback()
