# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Which ways of changing a closed month's books are blocked, and by what.

Each row is one write path of ERPNext v16, with the native control, the erpcore
control and the regression test that exercises it. "Covered" is used only
where a test proves the path is refused; "Native" where ERPNext alone refuses
it and a test proves that; "Allowed" where the path legitimately does not
change the closed month; "Not Covered" where nothing blocks it. Built from the
installed hooks where it can be, so a doctype another app adds to
`period_closing_doctypes` shows up here.
"""

import frappe
from frappe import _

TESTS = "erpcore/erp_core/monthly_close/tests/"


def native_doc() -> str:
	return _("Native: Accounting Period check on save (validate_accounting_period_on_doc_save)")


def native_gl() -> str:
	return _("Native: Accounting Period check on GL posting (general_ledger.validate_accounting_period)")


def guard(what: str = "GL/SLE/PLE") -> str:
	return _("erpcore: {0} before_submit gate (posting_guard)").format(what)


def execute(filters=None):
	columns = [
		{"fieldname": "path", "label": _("Write Path"), "fieldtype": "Data", "width": 300},
		{"fieldname": "effect", "label": _("Effect on Closed Month"), "fieldtype": "Data", "width": 220},
		{"fieldname": "native", "label": _("Native Control"), "fieldtype": "Data", "width": 300},
		{"fieldname": "erpcore", "label": _("erpcore Control"), "fieldtype": "Data", "width": 300},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "evidence", "label": _("Regression Test"), "fieldtype": "Data", "width": 320},
	]
	return columns, rows()


def row(path, effect, native, erpcore, status, evidence=""):
	return {
		"path": path,
		"effect": effect,
		"native": native,
		"erpcore": erpcore,
		"status": status,
		"evidence": evidence,
	}


def rows() -> list[dict]:
	closing = [
		dt for dt in dict.fromkeys(frappe.get_hooks("period_closing_doctypes")) if dt != "Bank Clearance"
	]
	data = []

	for doctype in closing:
		evidence = (
			TESTS + "test_hard_close.py::test_closed_month_refuses_postings_but_next_month_works"
			if doctype == "Journal Entry"
			else ""
		)
		data += [
			row(
				_("{0}: create or save a draft dated in the month").format(_(doctype)),
				_("None until submitted"),
				native_doc(),
				_("Drafts are reported by the draft check; a draft cannot change ledgers"),
				_("Native"),
				evidence,
			),
			row(
				_("{0}: submit").format(_(doctype)),
				_("Posts ledgers into the month"),
				native_doc() + " + " + native_gl(),
				guard(),
				_("Covered") if doctype == "Journal Entry" else _("Covered (untested type)"),
				evidence,
			),
			row(
				_("{0}: cancel").format(_(doctype)),
				_("Reverses ledgers dated in the month (immutable ledger off)"),
				native_gl(),
				guard(),
				_("Covered") if doctype == "Journal Entry" else _("Covered (untested type)"),
				evidence,
			),
			row(
				_("{0}: amend").format(_(doctype)),
				_("New draft; same as create, then submit"),
				native_doc(),
				guard(),
				_("Native"),
				"",
			),
		]

	data += [
		row(
			_("Bank Clearance / clearance date on Payment or Journal Entry"),
			_("Clearance metadata only; no ledger amounts"),
			_("Explicitly skipped by ERPNext"),
			_("Not blocked. Certified bank workpapers record their own values and are re-verified."),
			_("Allowed"),
		),
		row(
			_("Any other voucher that submits GL / Stock / Payment Ledger rows (custom or other apps)"),
			_("Changes the month's ledgers"),
			native_gl() + " " + _("(only for doctypes in Closed Documents)"),
			guard(),
			_("Covered"),
			TESTS + "test_posting_paths.py::test_ledger_rows_refused_in_locked_month",
		),
		row(
			_(
				"Payment Ledger Entry submitted into the month (reconciliation dated in the month, cancellation)"
			),
			_("Changes as-at-month-end outstanding (ageing)"),
			_("Not blocked natively"),
			guard("Payment Ledger Entry"),
			_("Covered"),
			TESTS + "test_posting_paths.py::test_ledger_rows_refused_in_locked_month",
		),
		row(
			_("Payment Ledger delink (unreconcile / cancel) of rows dated in the month"),
			_("Raw UPDATE of PLE/APLE delinked flag"),
			_("Not blocked natively"),
			_(
				"Always followed by a reversal PLE submit in the same transaction; refusing it rolls the delink back"
			),
			_("Covered"),
			TESTS + "test_posting_paths.py::test_ledger_rows_refused_in_locked_month",
		),
		row(
			_("Advance Payment Ledger Entry"),
			_("Advance allocation tracking"),
			_("Not blocked natively"),
			guard("Advance Payment Ledger Entry (dated by its voucher)"),
			_("Covered"),
			TESTS + "test_posting_paths.py::test_advance_ledger_dated_by_voucher",
		),
		row(
			_("Later settlement: payment dated after the month reconciled against an invoice of the month"),
			_("None on the closed month: allocation is dated on or after the payment"),
			_("ERPNext dates reconciliation at max(payment, invoice) or later"),
			_("Allowed; the live AR dashboard changes, the sealed packet does not"),
			_("Allowed"),
			TESTS + "test_posting_paths.py::test_later_settlement_is_allowed",
		),
		row(
			_("Stock posting in an earlier open month that revalues later SLEs of a locked month"),
			_("Rewrites the locked month's stock valuation (update_qty_in_future_sle, queued repost)"),
			_("Not blocked natively for later months"),
			guard("Stock Ledger Entry (later locked months, same item/warehouse)"),
			_("Covered"),
			TESTS + "test_posting_paths.py::test_backdated_stock_cannot_revalue_locked_month",
		),
		row(
			_("Repost Item Valuation starting on or before a locked month"),
			_("In-place SLE valuation update (update_sle_valuation_fields) and GL repost"),
			_("Native: checks only the repost's own date"),
			_(
				"erpcore: refused on validate if its date or any affected later item/warehouse is in a locked month"
			),
			_("Covered"),
			TESTS + "test_posting_paths.py::test_reposts_into_locked_month_are_refused",
		),
		row(
			_("Repost Accounting Ledger of a voucher in a locked month"),
			_("Rebuilds GL of the voucher in place"),
			_("Native: closed fiscal year / deferred checks only"),
			_("erpcore: refused on validate"),
			_("Covered"),
			TESTS + "test_posting_paths.py::test_reposts_into_locked_month_are_refused",
		),
		row(
			_("Repost Payment Ledger from a date on or before a locked month"),
			_("Rebuilds Payment Ledger of every later voucher"),
			_("None"),
			_("erpcore: refused on validate"),
			_("Covered"),
			TESTS + "test_posting_paths.py::test_reposts_into_locked_month_are_refused",
		),
		row(
			_("A repost queued or failed before the close, still unfinished"),
			_("May rewrite the month after checks ran"),
			_("None"),
			_("Stock check: unfinished reposts block and cannot be waived"),
			_("Covered"),
			TESTS + "test_checks_accuracy.py::test_unfinished_repost_is_non_waivable_blocker",
		),
		row(
			_("GL / PLE posting in an earlier reopened month"),
			_("Changes only opening balances of later locked months"),
			_("None for later months"),
			_(
				"Later Closed months are flagged for revalidation; revalidation compares sealed balances and reruns checks"
			),
			_("Covered"),
			TESTS + "test_hard_close.py::test_revalidation_requires_earlier_month_closed",
		),
		row(
			_("Scheduled depreciation, deferred accounting, exchange rate revaluation into the month"),
			_("Posts Journal Entries"),
			native_doc(),
			guard(),
			_("Covered (untested type)"),
		),
		row(
			_("Cancellation with immutable ledger enabled"),
			_("Reversal dated today, outside the month"),
			native_gl(),
			_("Not needed: the closed month is unchanged"),
			_("Allowed"),
		),
		row(
			_("Update after submit on vouchers of the month"),
			_("Depends on field; ledger amounts cannot change after submit"),
			_("Frappe allow_on_submit rules"),
			_("Not blocked unless it re-posts ledgers (then the ledger gate applies)"),
			_("Partial"),
		),
		row(
			_("Data Import / REST API of the vouchers above"),
			_("Same as the voucher"),
			native_doc(),
			guard(),
			_("Covered (same code path)"),
		),
		row(
			_("Direct SQL, bench console or privileged administrator edits"),
			_("Anything"),
			_("None"),
			_(
				"None. Fingerprints, evidence hashes and lock integrity checks may detect some changes afterwards."
			),
			_("Not Covered"),
		),
	]
	return data
