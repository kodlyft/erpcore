# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

TASK_FIELDS = (
	"task_key",
	"title",
	"category",
	"is_mandatory",
	"evidence_required",
	"manual_certification",
	"depends_on",
	"assigned_role",
	"due_offset_days",
	"description",
)


class MonthlyCloseTemplate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpcore.erp_core.doctype.monthly_close_template_task.monthly_close_template_task import (
			MonthlyCloseTemplateTask,
		)

		description: DF.SmallText | None
		tasks: DF.Table[MonthlyCloseTemplateTask]
		template_name: DF.Data
		version: DF.Int
	# end: auto-generated types

	def validate(self):
		self.validate_keys()
		self.bump_version()

	def validate_keys(self):
		keys = [row.task_key for row in self.tasks]
		duplicates = {key for key in keys if keys.count(key) > 1}
		if duplicates:
			frappe.throw(_("Task keys must be unique: {0}").format(", ".join(sorted(duplicates))))

		known = set(keys)
		for row in self.tasks:
			for dependency in split_keys(row.depends_on):
				if dependency not in known:
					frappe.throw(
						_("Row {0}: depends on unknown task key {1}.").format(
							row.idx, frappe.bold(dependency)
						)
					)
				if dependency == row.task_key:
					frappe.throw(_("Row {0}: a task cannot depend on itself.").format(row.idx))

	def bump_version(self):
		if self.is_new():
			self.version = self.version or 1
			return

		before = self.get_doc_before_save()
		if before and template_signature(before) != template_signature(self):
			self.version = cint(before.version) + 1


def template_signature(doc) -> list:
	return [[str(row.get(field) or "") for field in TASK_FIELDS] for row in doc.tasks]


def split_keys(value: str | None) -> list[str]:
	return [part.strip() for part in (value or "").split(",") if part.strip()]
