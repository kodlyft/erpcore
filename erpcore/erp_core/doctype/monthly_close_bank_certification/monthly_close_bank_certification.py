# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from erpcore.erp_core.doctype.monthly_close_task.monthly_close_task import require_private
from erpcore.erp_core.monthly_close.constants import BANK_CERT_DOCTYPE, IN_PROGRESS, TRANSITION_FLAG
from erpcore.erp_core.monthly_close.permissions import require_company_access, require_preparer


class MonthlyCloseBankCertification(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account: DF.Link | None
		account_currency: DF.Link | None
		bank_account: DF.Link
		certified_at: DF.Datetime | None
		certified_by: DF.Link | None
		company: DF.Link | None
		difference_explanation: DF.SmallText | None
		expected_statement_balance: DF.Currency
		incorrectly_cleared: DF.Currency
		ledger_balance: DF.Currency
		monthly_close: DF.Link
		outstanding_payments: DF.Currency
		outstanding_receipts: DF.Currency
		period_end: DF.Date | None
		prepared_by: DF.Link | None
		revision: DF.Int
		statement_balance: DF.Currency
		statement_date: DF.Date | None
		statement_file: DF.Attach | None
		status: DF.Literal["Draft", "Certified"]
		unexplained_difference: DF.Currency
	# end: auto-generated types

	def validate(self):
		close = frappe.db.get_value(
			"Monthly Close", self.monthly_close, ["company", "state", "revision", "period_end"], as_dict=True
		)
		self.company = close.company
		require_company_access(self.company)
		require_private(self.statement_file, _("The statement"))

		bank = frappe.db.get_value(
			"Bank Account", self.bank_account, ["company", "account", "is_company_account"], as_dict=True
		)
		if bank.company != self.company or not bank.is_company_account or not bank.account:
			frappe.throw(
				_("{0} is not a company bank account of {1} linked to a GL account.").format(
					self.bank_account, self.company
				)
			)
		self.account = bank.account

		if frappe.flags.get(TRANSITION_FLAG):
			return

		if close.state != IN_PROGRESS:
			frappe.throw(_("Bank workpapers can be edited only while the close is In Progress."))

		if self.is_new():
			self.revision = close.revision
			self.status = "Draft"
		else:
			before = self.get_doc_before_save()
			if before.status == "Certified":
				frappe.throw(
					_("This workpaper is certified. Changes after certification need a new revision.")
				)
			if cint(self.revision) != cint(close.revision):
				frappe.throw(_("This workpaper belongs to an earlier revision."))
			self.status = "Draft"

		self.prepared_by = frappe.session.user
		self.refresh_balances()

	def refresh_balances(self):
		"""Same arithmetic as ERPNext's Bank Reconciliation Statement, using its own query functions."""
		from erpnext.accounts.report.bank_reconciliation_statement.bank_reconciliation_statement import (
			get_amounts_not_reflected_in_system,
			get_entries,
		)
		from erpnext.accounts.utils import get_balance_on

		self.period_end = frappe.db.get_value("Monthly Close", self.monthly_close, "period_end")
		self.account_currency = frappe.get_cached_value("Account", self.account, "account_currency")
		filters = frappe._dict(
			account=self.account,
			report_date=self.period_end,
			company=self.company,
			include_pos_transactions=0,
		)

		entries = get_entries(filters)
		receipts = sum(flt(row.get("debit")) for row in entries)
		payments = sum(flt(row.get("credit")) for row in entries)
		precision = self.precision("ledger_balance")

		self.ledger_balance = flt(
			get_balance_on(self.account, self.period_end, company=self.company), precision
		)
		self.outstanding_receipts = flt(receipts, precision)
		self.outstanding_payments = flt(payments, precision)
		self.incorrectly_cleared = flt(get_amounts_not_reflected_in_system(filters), precision)
		self.expected_statement_balance = flt(
			self.ledger_balance - receipts + payments + self.incorrectly_cleared, precision
		)
		self.unexplained_difference = (
			flt(flt(self.statement_balance) - self.expected_statement_balance, precision)
			if self.statement_date
			else 0
		)

	def on_trash(self):
		if frappe.flags.get(TRANSITION_FLAG):
			return
		if self.status == "Certified":
			frappe.throw(_("A certified workpaper cannot be deleted."))


def prepare_for_close(close) -> list[str]:
	"""Create a draft workpaper for each company bank account that has none for this revision."""
	require_preparer()
	from erpcore.erp_core.monthly_close.checks.subledgers import company_bank_accounts

	created = []
	for account in company_bank_accounts(close.company):
		if frappe.db.exists(
			BANK_CERT_DOCTYPE,
			{"monthly_close": close.name, "revision": close.revision, "bank_account": account.name},
		):
			continue
		doc = frappe.get_doc(
			{"doctype": BANK_CERT_DOCTYPE, "monthly_close": close.name, "bank_account": account.name}
		)
		doc.insert()
		created.append(doc.name)
	return created
