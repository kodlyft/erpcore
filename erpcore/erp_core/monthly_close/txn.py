# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Transaction boundaries and read freshness.

MariaDB runs InnoDB at REPEATABLE READ. A transaction's first plain (non-locking)
SELECT creates a read view, and every later plain SELECT in that transaction
returns rows as they were at that moment, even after the transaction has taken
a row lock. Only locking reads (`FOR UPDATE`, `LOCK IN SHARE MODE`) return the
latest committed row. The close services therefore follow two rules:

* Request transitions call `fresh_read_view()` before taking the close lock.
  When the request has written nothing yet, it rolls back the empty
  transaction, so the next plain read opens a view that includes every commit
  up to the moment the lock is held. The caller's work is never committed. If
  the request has already written, the view cannot be replaced; the close row
  itself is then still loaded with a locking read, and child records are
  serialised behind the same close lock (see `posting_guard.acquire_close_gate`).
* Workers start each step with `begin()`, which commits the previous step, so
  each step reads a view opened after it started.

Workers commit between steps (see closing.py and check_runner.py). Under the
test runner those commits would leak fixtures out of the per-class rollback,
so in tests a step boundary is a savepoint instead. Concurrency tests that need
real, separately visible commits set `frappe.flags.erpcore_real_transactions`
and clean up after themselves.
"""

import frappe


def simulated() -> bool:
	in_test = getattr(frappe, "in_test", False) or frappe.flags.in_test
	return bool(in_test) and not frappe.flags.get("erpcore_real_transactions")


def begin(name: str) -> None:
	"""Start a step. In production this ends any earlier read view, so the next read sees all commits."""
	if simulated():
		frappe.db.savepoint(name)
	else:
		frappe.db.commit()  # nosemgrep


def commit() -> None:
	if not simulated():
		frappe.db.commit()  # nosemgrep


def rollback(name: str) -> None:
	if simulated():
		frappe.db.rollback(save_point=name)
	else:
		frappe.db.rollback()


def fresh_read_view() -> bool:
	"""Discard a read view opened by earlier plain reads, if that loses nothing.

	Returns True when the next plain read is guaranteed to open a new view.
	"""
	if simulated():
		return False
	if getattr(frappe.db, "transaction_writes", 1):
		return False
	frappe.db.rollback()
	return True
