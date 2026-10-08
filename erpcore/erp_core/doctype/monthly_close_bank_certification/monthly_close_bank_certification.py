# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from erpcore.erp_core.doctype.monthly_close_task.monthly_close_task import require_private
from erpcore.erp_core.monthly_close import evidence
from erpcore.erp_core.monthly_close.constants import BANK_CERT_DOCTYPE, IN_PROGRESS, TRANSITION_FLAG
from erpcore.erp_core.monthly_close.permissions import require_company_access, require_preparer
from erpcore.erp_core.monthly_close.posting_guard import acquire_close_gate

SCOPE_FIELDS = ("monthly_close", "company", "revision", "bank_account", "account", "account_currency")
SERVER_FIELDS = ("status", "prepared_by", "certified_by", "certified_at", "certified_hash", "statement_hash")
CERTIFIED_FIELDS = (
	*SCOPE_FIELDS,
	"period_end",
	"statement_date",
	"statement_balance",
	"statement_file",
	"statement_hash",
	"ledger_balance",
	"outstanding_receipts",
	"outstanding_payments",
	"incorrectly_cleared",
	"expected_statement_balance",
	"unexplained_difference",
	"difference_explanation",
	"bridging_note",
)

MAX_BRIDGING_DAYS = 31


def certified_content_hash(cert) -> str:
	return hashlib.sha256(
		json.dumps([str(cert.get(f) if cert.get(f) is not None else "") for f in CERTIFIED_FIELDS]).encode()
	).hexdigest()


class MonthlyCloseBankCertification(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account: DF.Link | None
		account_currency: DF.Link | None
		bank_account: DF.Link
		bridging_note: DF.SmallText | None
		certified_at: DF.Datetime | None
		certified_by: DF.Link | None
		certified_hash: DF.Data | None
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
		statement_hash: DF.Data | None
		status: DF.Literal["Draft", "Certified"]
		unexplained_difference: DF.Currency
	# end: auto-generated types

	def validate(self):
		gate = acquire_close_gate(self.monthly_close)
		close = frappe.db.get_value(
			"Monthly Close", self.monthly_close, ["period_start", "period_end"], as_dict=True
		)
		self.company = gate.company
		require_company_access(self.company, self.doctype)
		require_private(self.statement_file, _("The statement"))

		before = None if self.is_new() else self.get_doc_before_save()
		service = frappe.flags.get(TRANSITION_FLAG)
		if not service:
			for fieldname in SERVER_FIELDS:
				self.set(fieldname, before.get(fieldname) if before else None)

		bank = frappe.db.get_value(
			"Bank Account", self.bank_account, ["company", "account", "is_company_account"], as_dict=True
		)
		if not bank or bank.company != self.company or not bank.is_company_account or not bank.account:
			frappe.throw(
				_("{0} is not a company bank account of {1} linked to a GL account.").format(
					self.bank_account, self.company
				)
			)
		self.account = bank.account

		if service:
			return

		if gate.state != IN_PROGRESS:
			frappe.throw(_("Bank workpapers can be edited only while the close is In Progress."))

		if self.is_new():
			self.revision = gate.revision
		else:
			if before.status == "Certified":
				frappe.throw(
					_("This workpaper is certified. Changes after certification need a new revision.")
				)
			changed = [
				f
				for f in SCOPE_FIELDS
				if f != "account_currency" and str(before.get(f) or "") != str(self.get(f) or "")
			]
			if changed:
				frappe.throw(
					_("The close, revision and account of a workpaper cannot change: {0}.").format(
						", ".join(self.meta.get_label(f) for f in changed)
					),
					frappe.PermissionError,
				)
			if cint(self.revision) != cint(gate.revision):
				frappe.throw(_("This workpaper belongs to an earlier revision."))

		self.status = "Draft"
		self.prepared_by = frappe.session.user
		self.validate_statement(close)
		self.refresh_balances()

	def validate_statement(self, close):
		if self.statement_date:
			gap = abs((getdate(self.statement_date) - getdate(close.period_end)).days)
			if gap > MAX_BRIDGING_DAYS:
				frappe.throw(
					_(
						"The statement is dated {0}, more than {1} days from month end {2}. Attach the statement that covers month end."
					).format(self.statement_date, MAX_BRIDGING_DAYS, close.period_end)
				)

		if not self.statement_file:
			self.statement_hash = None
			return
		resolved = evidence.resolve(
			self.statement_file,
			_("The statement"),
			self.company,
			evidence.close_targets(self.doctype, self.name, self.monthly_close),
		)
		self.statement_hash = resolved.sha256

	def needs_bridging(self) -> bool:
		return bool(self.statement_date) and getdate(self.statement_date) != getdate(self.period_end)

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
			get_balance_on(self.account, self.period_end, company=self.company, in_account_currency=True),
			precision,
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
