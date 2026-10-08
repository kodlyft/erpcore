# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Which ways of changing a closed month's books are blocked, and by what.

Built from the installed hooks where it can be, so a doctype another app adds to
`period_closing_doctypes` shows up here. Paths that nothing blocks are listed
as such instead of being left out.
"""

import frappe
from frappe import _


def native_doc() -> str:
	return _("Native: Accounting Period check on save (validate_accounting_period_on_doc_save)")


def native_gl() -> str:
	return _(
		"Native: Accounting Period check on GL posting and reversal (general_ledger.validate_accounting_period)"
	)


def guard() -> str:
	return _("erpcore: GL/Stock Ledger serialization gate (posting_guard.guard_ledger_entry)")


def execute(filters=None):
	columns = [
		{"fieldname": "path", "label": _("Posting Path"), "fieldtype": "Data", "width": 300},
		{"fieldname": "effect", "label": _("Effect on Closed Month"), "fieldtype": "Data", "width": 220},
		{"fieldname": "native", "label": _("Native Control"), "fieldtype": "Data", "width": 320},
		{"fieldname": "erpcore", "label": _("erpcore Control"), "fieldtype": "Data", "width": 280},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
	]
	return columns, rows()


def rows() -> list[dict]:
	closing = list(dict.fromkeys(frappe.get_hooks("period_closing_doctypes")))
	data = []

	for doctype in closing:
		data.append(
			{
				"path": _("{0}: create/save draft, submit, cancel, amend, date change").format(_(doctype)),
				"effect": _("Changes the month's ledgers")
				if doctype != "Bank Clearance"
				else _("Clearance metadata"),
				"native": native_doc() if doctype != "Bank Clearance" else _("Explicitly skipped by ERPNext"),
				"erpcore": guard(),
				"status": _("Covered") if doctype != "Bank Clearance" else _("Allowed"),
			}
		)

	data += [
		{
			"path": _("Any other voucher that posts GL Entries (custom or other apps)"),
			"effect": _("Changes the month's GL"),
			"native": native_gl() + " " + _("(only for doctypes listed in Closed Documents)"),
			"erpcore": guard(),
			"status": _("Covered"),
		},
		{
			"path": _("Scheduled depreciation, deferred accounting, exchange rate revaluation"),
			"effect": _("Posts Journal Entries"),
			"native": native_doc(),
			"erpcore": guard(),
			"status": _("Covered"),
		},
		{
			"path": _("Repost Item Valuation / repost accounting ledger into the month"),
			"effect": _("Rewrites stock and GL rows"),
			"native": _("Native: Repost Item Valuation validates the Accounting Period"),
			"erpcore": guard() + " " + _("+ pending reposts block the stock check"),
			"status": _("Covered"),
		},
		{
			"path": _("Cancellation with immutable ledger enabled"),
			"effect": _("Reversal dated today, outside the month"),
			"native": native_gl(),
			"erpcore": _("Not needed: the closed month is unchanged"),
			"status": _("Allowed"),
		},
		{
			"path": _("Bank clearance date on Payment/Journal Entry"),
			"effect": _("Clearance metadata only; no ledger amounts"),
			"native": _("Not blocked"),
			"erpcore": _("Not blocked. Bank certifications record their own as-at values."),
			"status": _("Allowed"),
		},
		{
			"path": _("Payment reconciliation / unreconciliation against invoices of the month"),
			"effect": _("Changes Payment Ledger allocation (ageing), not GL balances"),
			"native": _("Not blocked unless it creates GL in the month"),
			"erpcore": _(
				"GL in the month is blocked by the gate; allocation-only changes are allowed and show in the next fingerprint"
			),
			"status": _("Partial"),
		},
		{
			"path": _("Update after submit on vouchers of the month"),
			"effect": _("Depends on field; ledger amounts cannot change after submit"),
			"native": _("Frappe allow_on_submit rules"),
			"erpcore": _("Not blocked unless it re-posts ledgers"),
			"status": _("Partial"),
		},
		{
			"path": _("Data Import / REST API of the vouchers above"),
			"effect": _("Same as the voucher"),
			"native": native_doc(),
			"erpcore": guard(),
			"status": _("Covered"),
		},
		{
			"path": _("Direct SQL, bench console or privileged administrator edits"),
			"effect": _("Anything"),
			"native": _("None"),
			"erpcore": _("None. Fingerprint and lock integrity checks may detect some changes afterwards."),
			"status": _("Not Covered"),
		},
	]
	return data
