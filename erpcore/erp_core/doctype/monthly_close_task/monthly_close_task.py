# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from erpcore.erp_core.monthly_close import evidence
from erpcore.erp_core.monthly_close.constants import IN_PROGRESS, ROLE_MANAGER, ROLE_PREPARER, TRANSITION_FLAG
from erpcore.erp_core.monthly_close.permissions import require_company_access
from erpcore.erp_core.monthly_close.posting_guard import acquire_close_gate

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
	"assigned_role",
)
SERVER_FIELDS = ("completed_by", "completed_at", "evidence_hash")


def require_private(file_url: str | None, label: str) -> None:
	"""Cheap pre-check for a private URL. `evidence.resolve` does the real verification."""
	if file_url and not file_url.startswith("/private/"):
		frappe.throw(
			_("{0} must be attached as a private file.").format(label),
			title=_("Private Evidence Required"),
		)


def can_work_on(task, user: str | None = None) -> bool:
	"""Assignment policy: the assignee, else holders of the assigned role, else preparers. Managers always."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if ROLE_MANAGER in roles:
		return True
	if task.assigned_to:
		return task.assigned_to == user
	if task.assigned_role:
		return task.assigned_role in roles
	return ROLE_PREPARER in roles


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
		evidence_hash: DF.Data | None
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
		close = acquire_close_gate(self.monthly_close)
		self.company = close.company
		require_company_access(self.company, self.doctype)

		before = None if self.is_new() else self.get_doc_before_save()
		for fieldname in SERVER_FIELDS:
			self.set(fieldname, before.get(fieldname) if before else None)

		service = frappe.flags.get(TRANSITION_FLAG)
		if self.is_new() and not service:
			self.prepare_ad_hoc(close)
		elif not service:
			self.protect_definition(close, before)

		if not service:
			self.authorize(before)

		self.validate_evidence(before)
		self.validate_status(before)

	def authorize(self, before):
		"""Judge the change against the assignment as stored, so nobody can assign a task to
		themselves and complete it in the same save."""
		current = before or self
		if not can_work_on(current):
			frappe.throw(
				_("Task {0} is assigned to {1}. Only the assignee or a Close Manager can work on it.").format(
					frappe.bold(self.title),
					current.assigned_to or _(current.assigned_role or "Close Preparer"),
				),
				frappe.PermissionError,
				title=_("Not Assigned"),
			)

		if before and (before.assigned_to or "") != (self.assigned_to or ""):
			if frappe.session.user != "Administrator" and ROLE_MANAGER not in frappe.get_roles():
				frappe.throw(_("Only a Close Manager can reassign a close task."), frappe.PermissionError)

	def validate_evidence(self, before):
		require_private(self.evidence, _("Evidence"))
		if not self.evidence:
			self.evidence_hash = None
			return

		if before and before.evidence == self.evidence and before.evidence_hash:
			problem = evidence.verify(self.evidence, before.evidence_hash)
			if problem and before.status == "Done":
				frappe.throw(
					_("Evidence of {0}: {1}. Attach it again.").format(frappe.bold(self.title), problem),
					evidence.EvidenceError,
				)
			if not problem:
				return

		resolved = evidence.resolve(
			self.evidence,
			_("Evidence"),
			self.company,
			evidence.close_targets(self.doctype, self.name, self.monthly_close),
		)
		self.evidence_hash = resolved.sha256

	def prepare_ad_hoc(self, close):
		"""Preparers may add extra tasks to a running close; they join the current revision."""
		if close.state != IN_PROGRESS:
			frappe.throw(_("Tasks can be added only while the close is In Progress."))
		self.revision = close.revision
		self.task_key = self.task_key or f"adhoc-{frappe.scrub(self.title)[:40]}"

	def protect_definition(self, close, before):
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

	def validate_status(self, before):
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

		if not before or before.status != "Done":
			self.completed_by = frappe.session.user
			self.completed_at = now_datetime()

	def open_dependencies(self) -> list[str]:
		keys = [k.strip() for k in (self.depends_on or "").split(",") if k.strip()]
		if not keys:
			return []
		task = frappe.qb.DocType("Monthly Close Task")
		return (
			frappe.qb.from_(task)
			.select(task.title)
			.where(task.monthly_close == self.monthly_close)
			.where(task.revision == self.revision)
			.where(task.task_key.isin(keys))
			.where(task.status == "Open")
			.for_update()
			.run(pluck=True)
		)

	def on_trash(self):
		if frappe.flags.get(TRANSITION_FLAG):
			return
		frappe.throw(_("Checklist tasks are part of the close record and cannot be deleted."))
