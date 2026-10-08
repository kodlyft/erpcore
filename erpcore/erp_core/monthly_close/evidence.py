# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Evidence attachments: resolution, hashing and protection.

A URL string is not evidence. Evidence on a task, bank workpaper or exception
must resolve to an existing private File that:

* the current user may read (native File permission, which follows the record
  it is attached to; a `/private/` URL by itself grants nothing),
* is attached to the record itself, to its Monthly Close, or to another
  monthly close record of the same company (shared evidence, e.g. one bank
  statement used by two workpapers),
* has readable content, whose SHA-256 is stored next to the URL.

Once a record certifies evidence, the File cannot be deleted, made public or
repointed through the normal API. Frappe's File controller moves or deletes the
bytes on disk inside its own `validate` / `on_trash`, before ordinary document
hooks run and beyond the reach of a rollback, so the guards sit earlier:

* delete: `file_has_permission` denies `delete` (checked by `frappe.delete_doc`
  before `on_trash`); `protect_evidence_file_delete` is a backstop;
* make public / change URL: `protect_evidence_file` on `before_validate`.

Code that deletes with `ignore_permissions` bypasses the first guard. If bytes
or records change behind the API anyway, `verify()` reports it and the close
treats the task, workpaper or exception as no longer valid.
"""

import hashlib

import frappe
from frappe import _

from erpcore.erp_core.monthly_close.constants import (
	BANK_CERT_DOCTYPE,
	CLOSE_DOCTYPE,
	COMPANY_SCOPED_DOCTYPES,
	EXCEPTION_DOCTYPE,
	SNAPSHOT_DOCTYPE,
	TASK_DOCTYPE,
	TRANSITION_FLAG,
)

# (doctype, file field, hash field) of every record that can certify evidence.
EVIDENCE_FIELDS = (
	(TASK_DOCTYPE, "evidence", "evidence_hash"),
	(BANK_CERT_DOCTYPE, "statement_file", "statement_hash"),
	(EXCEPTION_DOCTYPE, "evidence", "evidence_hash"),
	(SNAPSHOT_DOCTYPE, "file", "sha256"),
)


class EvidenceError(frappe.ValidationError):
	pass


def sha256_of(content) -> str:
	if isinstance(content, str):
		content = content.encode()
	return hashlib.sha256(content or b"").hexdigest()


def file_content(file_doc) -> bytes:
	try:
		content = file_doc.get_content()
	except Exception:
		frappe.throw(
			_("The file {0} cannot be read from storage.").format(frappe.bold(file_doc.file_name)),
			EvidenceError,
		)
	if isinstance(content, str):
		content = content.encode()
	return content


def resolve(file_url: str, label: str, company: str, targets: list[tuple[str, str]]) -> frappe._dict:
	"""Resolve an evidence URL to a readable private File. Returns {name, file_url, sha256}.

	`targets` are (doctype, name) pairs the file may be attached to directly.
	"""
	if not file_url or not file_url.startswith("/private/files/"):
		frappe.throw(
			_("{0} must be attached as a private file.").format(label),
			EvidenceError,
			title=_("Private Evidence Required"),
		)

	candidates = frappe.get_all(
		"File",
		filters={"file_url": file_url, "is_folder": 0},
		fields=["name", "is_private", "attached_to_doctype", "attached_to_name"],
		order_by="creation asc",
	)
	if not candidates:
		frappe.throw(_("{0}: no file exists at {1}.").format(label, file_url), EvidenceError)

	allowed = {(dt, dn) for dt, dn in targets if dt and dn}

	def scope(row) -> int:
		if (row.attached_to_doctype, row.attached_to_name) in allowed:
			return 2
		if row.attached_to_doctype in COMPANY_SCOPED_DOCTYPES and row.attached_to_name:
			owner_company = frappe.db.get_value(row.attached_to_doctype, row.attached_to_name, "company")
			if owner_company == company:
				return 1
		return 0

	ranked = sorted(((scope(row), row) for row in candidates), key=lambda pair: -pair[0])
	best_scope, row = ranked[0]
	if not best_scope:
		frappe.throw(
			_(
				"{0} must be uploaded to this record or to a monthly close record of {1}. Files attached elsewhere are not accepted as close evidence."
			).format(label, frappe.bold(company)),
			EvidenceError,
		)
	if not row.is_private:
		frappe.throw(_("{0} must be a private file.").format(label), EvidenceError)

	file_doc = frappe.get_doc("File", row.name)
	if not frappe.has_permission("File", "read", doc=file_doc):
		frappe.throw(_("You cannot read the file attached as {0}.").format(label), frappe.PermissionError)

	return frappe._dict(name=file_doc.name, file_url=file_url, sha256=sha256_of(file_content(file_doc)))


def verify(file_url: str | None, expected: str | None) -> str | None:
	"""Problem with certified evidence, or None when it is intact."""
	if not file_url:
		return _("evidence missing")
	if not expected:
		return _("evidence was never verified")
	name = frappe.db.get_value("File", {"file_url": file_url, "is_private": 1, "is_folder": 0}, "name")
	if not name:
		return _("evidence file was deleted or made public")
	try:
		actual = sha256_of(frappe.get_doc("File", name).get_content())
	except Exception:
		return _("evidence file cannot be read")
	if actual != expected:
		return _("evidence file content changed after it was attached")
	return None


def references(file_url: str) -> list[tuple[str, str]]:
	"""Records that hold this URL as verified evidence."""
	found = []
	for doctype, field, hash_field in EVIDENCE_FIELDS:
		if not frappe.db.table_exists(doctype):
			continue
		for name in frappe.get_all(
			doctype, filters={field: file_url, hash_field: ["is", "set"]}, pluck="name", limit=5
		):
			found.append((doctype, name))
	return found


def file_has_permission(doc, ptype=None, user=None, debug=False) -> bool:
	"""`has_permission` hook on File: no `delete` of certified evidence. Otherwise no objection."""
	if ptype != "delete" or frappe.flags.get(TRANSITION_FLAG):
		return True
	file_url = (
		doc.get("file_url") if not isinstance(doc, str) else frappe.db.get_value("File", doc, "file_url")
	)
	if not (file_url or "").startswith("/private/"):
		return True
	return not references(file_url)


def protect_evidence_file(doc, method=None):
	"""File `before_validate`: verified evidence stays private and keeps its URL."""
	if doc.is_new() or frappe.flags.get(TRANSITION_FLAG):
		return
	before = doc.get_doc_before_save()
	if not before or not (before.file_url or "").startswith("/private/"):
		return
	if before.file_url == doc.file_url and int(doc.is_private or 0) == int(before.is_private or 0):
		return

	used_by = references(before.file_url)
	if used_by:
		frappe.throw(
			_("This file is certified evidence for {0}. It cannot be made public or replaced.").format(
				", ".join(f"{_(dt)} {dn}" for dt, dn in used_by)
			),
			frappe.PermissionError,
			title=_("Protected Evidence"),
		)


def protect_evidence_file_delete(doc, method=None):
	if frappe.flags.get(TRANSITION_FLAG) or not (doc.file_url or "").startswith("/private/"):
		return
	used_by = references(doc.file_url)
	if used_by:
		frappe.throw(
			_("This file is certified evidence for {0} and cannot be deleted.").format(
				", ".join(f"{_(dt)} {dn}" for dt, dn in used_by)
			),
			frappe.PermissionError,
			title=_("Protected Evidence"),
		)


def close_targets(record_doctype: str, record_name: str | None, close_name: str) -> list[tuple[str, str]]:
	return [(record_doctype, record_name), (CLOSE_DOCTYPE, close_name)]
