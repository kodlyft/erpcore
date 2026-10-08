# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from erpcore.erp_core.monthly_close.constants import (
	DRAFT,
	EDITABLE_STATES,
	EVENT_DOCTYPE,
	POLICY_DOCTYPE,
	TRANSITION_FLAG,
)
from erpcore.erp_core.monthly_close.periods import fiscal_year_for, month_bounds, month_label
from erpcore.erp_core.monthly_close.permissions import require_company_access

# The only fields people edit directly. Everything else is written by the close service.
USER_EDITABLE_FIELDS = frozenset({"preparer", "reviewer", "due_date", "notes", "template"})
IGNORED_FIELDS = frozenset(
	{"modified", "modified_by", "dashboard_html", "_comments", "_assign", "_liked_by", "_user_tags"}
)


class MonthlyClose(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		accounting_period: DF.Link | None
		approved_at: DF.Datetime | None
		approved_by: DF.Link | None
		approved_check_run: DF.Link | None
		approved_fingerprint: DF.Data | None
		approved_revision: DF.Int
		closed_at: DF.Datetime | None
		closed_by: DF.Link | None
		closer: DF.Link | None
		closing_check_run: DF.Link | None
		closing_fingerprint: DF.Data | None
		company: DF.Link
		due_date: DF.Date | None
		external_accounting_period: DF.Link | None
		fiscal_year: DF.Link | None
		last_error: DF.SmallText | None
		latest_check_run: DF.Link | None
		latest_fingerprint: DF.Data | None
		lock_owned: DF.Check
		month: DF.Data | None
		notes: DF.TextEditor | None
		pending_action: DF.Data | None
		pending_attempt: DF.Int
		pending_job_id: DF.Data | None
		pending_requested_at: DF.Datetime | None
		pending_requested_by: DF.Link | None
		pending_started_at: DF.Datetime | None
		pending_token: DF.Data | None
		period_end: DF.Date | None
		period_start: DF.Date
		policy: DF.Link | None
		policy_hash: DF.Data | None
		policy_snapshot: DF.Code | None
		policy_version: DF.Int
		preparer: DF.Link | None
		rejection_reason: DF.SmallText | None
		reopen_count: DF.Int
		reopened_at: DF.Datetime | None
		reopened_by: DF.Link | None
		revalidated_at: DF.Datetime | None
		revalidated_by: DF.Link | None
		revalidation_required: DF.Check
		reviewer: DF.Link | None
		revision: DF.Int
		self_approval_used: DF.Check
		state: DF.Literal[
			"Draft", "In Progress", "Ready for Review", "Approved", "Closing", "Closed", "Reopened"
		]
		submitted_at: DF.Datetime | None
		submitted_by: DF.Link | None
		submitted_check_run: DF.Link | None
		template: DF.Link | None
		template_snapshot: DF.Code | None
		template_version: DF.Int
	# end: auto-generated types

	def autoname(self):
		start = month_bounds(self.period_start)[0]
		abbr = frappe.get_cached_value("Company", self.company, "abbr")
		self.name = f"MC-{abbr}-{month_label(start)}"

	def validate(self):
		require_company_access(self.company)
		if self.is_new():
			self.initialise()
		else:
			self.protect_controlled_fields()

	def initialise(self):
		"""A new close always starts as revision 1 Draft, whatever the request said."""
		start, end = month_bounds(self.period_start)
		self.period_start, self.period_end = start, end
		self.month = month_label(start)
		self.fiscal_year = fiscal_year_for(self.company, start)

		if not frappe.db.exists(POLICY_DOCTYPE, self.company):
			frappe.throw(_("Create a Monthly Close Policy for {0} first.").format(frappe.bold(self.company)))

		policy = frappe.get_cached_doc(POLICY_DOCTYPE, self.company)
		if not policy.enabled:
			frappe.throw(_("The Monthly Close Policy for {0} is disabled.").format(frappe.bold(self.company)))
		if policy.cutover_period and start < getdate(policy.cutover_period):
			frappe.throw(
				_("{0} is before the first managed month {1}.").format(
					month_label(start), month_label(policy.cutover_period)
				)
			)

		if not frappe.flags.get(TRANSITION_FLAG):
			for field in self.meta.fields:
				if field.read_only and field.fieldname not in ("period_end", "month", "fiscal_year"):
					self.set(field.fieldname, field.default if field.default is not None else None)

		self.state = DRAFT
		self.revision = 1
		self.policy = policy.name
		self.policy_version = policy.policy_version
		self.template = self.template or policy.template

	def protect_controlled_fields(self):
		if frappe.flags.get(TRANSITION_FLAG):
			return

		before = self.get_doc_before_save()
		if not before:
			return

		changed = {
			df.fieldname
			for df in self.meta.fields
			if df.fieldname not in IGNORED_FIELDS
			and df.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML")
			and str(before.get(df.fieldname) or "") != str(self.get(df.fieldname) or "")
		}
		if not changed:
			return

		controlled = changed - USER_EDITABLE_FIELDS
		if controlled:
			frappe.throw(
				_("{0} can only change through the close actions.").format(
					", ".join(_(self.meta.get_label(f)) for f in sorted(controlled))
				),
				frappe.PermissionError,
				title=_("Controlled Field"),
			)

		if self.state not in EDITABLE_STATES:
			frappe.throw(
				_(
					"{0} is {1}; its team and notes are frozen. Send it back or reopen it to change them."
				).format(self.name, _(self.state)),
				title=_("Close Frozen"),
			)

		if "template" in changed and self.state != DRAFT:
			frappe.throw(_("The checklist template can only change before the close is started."))

	def on_trash(self):
		if self.state != DRAFT and not frappe.flags.get(TRANSITION_FLAG):
			frappe.throw(
				_("Only a Draft close can be deleted. {0} is {1}; its history is kept.").format(
					self.name, _(self.state)
				),
				title=_("Cannot Delete"),
			)

		previous = frappe.flags.get(TRANSITION_FLAG)
		frappe.flags[TRANSITION_FLAG] = True
		try:
			for event in frappe.get_all(EVENT_DOCTYPE, filters={"monthly_close": self.name}, pluck="name"):
				frappe.delete_doc(EVENT_DOCTYPE, event, ignore_permissions=True, force=True)
		finally:
			frappe.flags[TRANSITION_FLAG] = previous
