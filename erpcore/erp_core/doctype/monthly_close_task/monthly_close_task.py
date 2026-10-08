# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from erpcore.erp_core.monthly_close.constants import IN_PROGRESS, TRANSITION_FLAG
from erpcore.erp_core.monthly_close.permissions import require_company_access

# Copied from the template when the close starts; never edited afterwards.
DEFINITION_FIELDS = (
	"monthly_close",
	"company",
	"revision",
	"task_key",
	"title",
	"category",
	"description",
	"is_mandatory",
	"evidence_required",
	"manual_certification",
	"depends_on",
)


def require_private(file_url: str | None, label: str) -> None:
	"""Evidence must be a private file, so it is served only to users who can read the record."""
	if file_url and not file_url.startswith("/private/"):
		frappe.throw(
			_("{0} must be attached as a private file.").format(label),
			title=_("Private Evidence Required"),
		)


class MonthlyCloseTask(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		assigned_role: DF.Link | None
		assigned_to: DF.Link | None
		category: DF.Literal[
			"Preparation",
			"Reconciliation",
			"Accruals",
			"Tax",
			"Payroll",
			"Intercompany",
			"Review",
			"Approval",
			"Other",
		]
		company: DF.Link | None
		completed_at: DF.Datetime | None
		completed_by: DF.Link | None
		depends_on: DF.Data | None
		description: DF.SmallText | None
		due_date: DF.Date | None
		evidence: DF.Attach | None
		evidence_required: DF.Check
		is_mandatory: DF.Check
		manual_certification: DF.Check
		monthly_close: DF.Link
		notes: DF.SmallText | None
		revision: DF.Int
		status: DF.Literal["Open", "Done", "Not Applicable"]
		task_key: DF.Data | None
		title: DF.Data
	# end: auto-generated types

	def validate(self):
		close = frappe.db.get_value(
			"Monthly Close", self.monthly_close, ["company", "state", "revision"], as_dict=True
		)
		self.company = close.company
		require_company_access(self.company)

		service = frappe.flags.get(TRANSITION_FLAG)
		if self.is_new() and not service:
			self.prepare_ad_hoc(close)
		elif not service:
			self.protect_definition(close)

		require_private(self.evidence, _("Evidence"))
		self.validate_status()

	def prepare_ad_hoc(self, close):
		"""Preparers may add extra tasks to a running close; they join the current revision."""
		if close.state != IN_PROGRESS:
			frappe.throw(_("Tasks can be added only while the close is In Progress."))
		self.revision = close.revision
		self.task_key = self.task_key or f"adhoc-{frappe.scrub(self.title)[:40]}"

	def protect_definition(self, close):
		before = self.get_doc_before_save()
		changed = [f for f in DEFINITION_FIELDS if str(before.get(f) or "") != str(self.get(f) or "")]
		if changed:
			frappe.throw(
				_("The task definition comes from the frozen checklist and cannot change: {0}.").format(
					", ".join(self.meta.get_label(f) for f in changed)
				),
				frappe.PermissionError,
			)

		if close.state != IN_PROGRESS or cint(self.revision) != cint(close.revision):
			frappe.throw(
				_("The checklist of {0} is frozen while it is {1}.").format(
					self.monthly_close, _(close.state)
				),
				title=_("Checklist Frozen"),
			)

	def validate_status(self):
		if self.status == "Not Applicable" and self.is_mandatory:
			frappe.throw(_("A mandatory task cannot be marked Not Applicable."))

		if self.status != "Done":
			self.completed_by = None
			self.completed_at = None
			return

		if self.evidence_required and not self.evidence:
			frappe.throw(_("Attach the evidence before marking {0} done.").format(frappe.bold(self.title)))

		if self.manual_certification and not (self.notes or "").strip():
			frappe.throw(
				_("Record what you reviewed in Notes before certifying {0}.").format(frappe.bold(self.title))
			)

		pending = self.open_dependencies()
		if pending:
			frappe.throw(_("Finish these tasks first: {0}.").format(", ".join(pending)))

		if not self.completed_by:
			self.completed_by = frappe.session.user
			self.completed_at = now_datetime()

	def open_dependencies(self) -> list[str]:
		keys = [k.strip() for k in (self.depends_on or "").split(",") if k.strip()]
		if not keys:
			return []
		return frappe.get_all(
			"Monthly Close Task",
			filters={
				"monthly_close": self.monthly_close,
				"revision": self.revision,
				"task_key": ["in", keys],
				"status": "Open",
			},
			pluck="title",
		)

	def on_trash(self):
		if frappe.flags.get(TRANSITION_FLAG):
			return
		frappe.throw(_("Checklist tasks are part of the close record and cannot be deleted."))
