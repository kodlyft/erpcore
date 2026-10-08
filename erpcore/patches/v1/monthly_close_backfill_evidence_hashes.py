# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Record content hashes for evidence attached before hashes existed.

Without this, every Done task with evidence, every attached bank statement and every
exception with evidence from before the upgrade would count as "never verified".
Only URLs that resolve to an existing private File are hashed; anything else is left
empty and shows up as missing evidence for a person to fix. Certified bank workpapers
get their certified-content hash from their values as they are at upgrade time.
"""

import frappe

from erpcore.erp_core.monthly_close.evidence import sha256_of

TARGETS = (
	("Monthly Close Task", "evidence", "evidence_hash"),
	("Monthly Close Bank Certification", "statement_file", "statement_hash"),
	("Monthly Close Exception", "evidence", "evidence_hash"),
)


def file_hash(file_url: str) -> str | None:
	name = frappe.db.get_value("File", {"file_url": file_url, "is_private": 1, "is_folder": 0}, "name")
	if not name:
		return None
	try:
		return sha256_of(frappe.get_doc("File", name).get_content())
	except Exception:
		return None


def execute():
	for doctype, url_field, hash_field in TARGETS:
		if not frappe.db.has_column(doctype, hash_field):
			continue
		for row in frappe.get_all(
			doctype,
			filters={url_field: ["like", "/private/files/%"], hash_field: ["is", "not set"]},
			fields=["name", url_field],
		):
			digest = file_hash(row.get(url_field))
			if digest:
				frappe.db.set_value(doctype, row.name, hash_field, digest, update_modified=False)

	from erpcore.erp_core.doctype.monthly_close_bank_certification.monthly_close_bank_certification import (
		certified_content_hash,
	)

	for name in frappe.get_all(
		"Monthly Close Bank Certification",
		filters={"status": "Certified", "certified_hash": ["is", "not set"]},
		pluck="name",
	):
		doc = frappe.get_doc("Monthly Close Bank Certification", name)
		frappe.db.set_value(
			doc.doctype, name, "certified_hash", certified_content_hash(doc), update_modified=False
		)
