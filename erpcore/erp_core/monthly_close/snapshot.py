# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Financial snapshots for the close packet.

The installed ERPNext reports are executed through their own `execute()` with
explicit filters, so the figures use ERPNext's formulas, not copies of them.
Each result is stored as a private JSON file holding the columns, rows and
filters, the app versions and the close revision. The SHA-256 of the file is
recorded on the Snapshot row.

The hash detects accidental or casual alteration. It is not a legal digital
signature and gives no protection against someone with database and file
system access.

Monthly figures and year-to-date figures are separate, labelled snapshots. The
Balance Sheet is as at month end, from the fiscal year start. Snapshots use the
default book (`include_default_book_entries`) with no finance-book or dimension
filter; those filters narrow reports and workpapers but never the lock, which
is company-wide.
"""

import hashlib
import json

import frappe
from frappe.utils import cint, now_datetime

from erpcore.erp_core.monthly_close.check_runner import as_system_user
from erpcore.erp_core.monthly_close.constants import SNAPSHOT_DOCTYPE, SNAPSHOT_ORIGINAL
from erpcore.erp_core.monthly_close.periods import fiscal_year_start

REPORT_MODULES = {
	"Trial Balance": "erpnext.accounts.report.trial_balance.trial_balance",
	"Profit and Loss Statement": "erpnext.accounts.report.profit_and_loss_statement.profit_and_loss_statement",
	"Balance Sheet": "erpnext.accounts.report.balance_sheet.balance_sheet",
	"Accounts Receivable Summary": "erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary",
	"Accounts Payable Summary": "erpnext.accounts.report.accounts_payable_summary.accounts_payable_summary",
	"Stock and Account Value Comparison": "erpnext.stock.report.stock_and_account_value_comparison.stock_and_account_value_comparison",
}


def report_specs(close) -> list[dict]:
	import erpnext

	start, end, fy = close.period_start, close.period_end, close.fiscal_year
	fy_start = fiscal_year_start(fy)
	common = {"company": close.company, "include_default_book_entries": 1}
	statement = {
		**common,
		"filter_based_on": "Date Range",
		"from_fiscal_year": fy,
		"to_fiscal_year": fy,
	}
	ageing = {
		"company": close.company,
		"report_date": end,
		"ageing_based_on": "Due Date",
		"age_as_on": "Report Date",
		"range": "30, 60, 90, 120",
	}

	specs = [
		{
			"report": "Trial Balance",
			"scope": "Month",
			"from_date": start,
			"to_date": end,
			"filters": {
				**common,
				"fiscal_year": fy,
				"from_date": start,
				"to_date": end,
				"show_zero_values": 0,
			},
		},
		{
			"report": "Profit and Loss Statement",
			"scope": "Month",
			"from_date": start,
			"to_date": end,
			"filters": {
				**statement,
				"period_start_date": start,
				"period_end_date": end,
				"periodicity": "Monthly",
				"accumulated_values": 0,
			},
		},
		{
			"report": "Profit and Loss Statement",
			"scope": "Year to Date",
			"from_date": fy_start,
			"to_date": end,
			"filters": {
				**statement,
				"period_start_date": fy_start,
				"period_end_date": end,
				"periodicity": "Yearly",
				"accumulated_values": 1,
			},
		},
		{
			"report": "Balance Sheet",
			"scope": "As at Month End",
			"from_date": fy_start,
			"to_date": end,
			"filters": {
				**statement,
				"period_start_date": fy_start,
				"period_end_date": end,
				"periodicity": "Yearly",
				"accumulated_values": 1,
			},
		},
		{
			"report": "Accounts Receivable Summary",
			"scope": "As at Month End",
			"to_date": end,
			"filters": ageing,
		},
		{"report": "Accounts Payable Summary", "scope": "As at Month End", "to_date": end, "filters": ageing},
	]

	if erpnext.is_perpetual_inventory_enabled(close.company) and frappe.db.exists(
		"Stock Ledger Entry", {"company": close.company, "is_cancelled": 0}
	):
		specs.append(
			{
				"report": "Stock and Account Value Comparison",
				"scope": "Month",
				"from_date": start,
				"to_date": end,
				"filters": {"company": close.company, "from_date": start, "as_on_date": end},
			}
		)

	return specs


def app_versions() -> dict:
	versions = {}
	for app in ("frappe", "erpnext", "erpcore"):
		try:
			versions[app] = frappe.get_attr(f"{app}.__version__")
		except Exception:
			versions[app] = None
	return versions


def _run(spec) -> tuple[list, list]:
	execute = frappe.get_attr(f"{REPORT_MODULES[spec['report']]}.execute")
	result = execute(frappe._dict(spec["filters"])) or ([], [])
	columns, rows = result[0] or [], result[1] or []
	return (
		[c if isinstance(c, dict) else {"label": str(c)} for c in columns],
		[r if isinstance(r, dict) else list(r) for r in rows],
	)


def payload_bytes(payload: dict) -> bytes:
	return json.dumps(payload, default=str, sort_keys=True, indent=1).encode()


def sha256(content: bytes) -> str:
	return hashlib.sha256(content).hexdigest()


def capture(close) -> list[str]:
	"""Run every report for the locked month and store it. Runs inside the seal transaction."""
	versions = app_versions()
	created = []

	with as_system_user():
		for spec in report_specs(close):
			columns, rows = _run(spec)
			payload = {
				"close": close.name,
				"company": close.company,
				"revision": close.revision,
				"report": spec["report"],
				"scope": spec["scope"],
				"filters": spec["filters"],
				"generated_at": now_datetime(),
				"app_versions": versions,
				"columns": columns,
				"rows": rows,
			}
			content = payload_bytes(payload)
			digest = sha256(content)

			snap = frappe.get_doc(
				{
					"doctype": SNAPSHOT_DOCTYPE,
					"monthly_close": close.name,
					"company": close.company,
					"revision": close.revision,
					"report_name": spec["report"],
					"scope": spec["scope"],
					"from_date": spec.get("from_date"),
					"to_date": spec.get("to_date"),
					"filters_json": json.dumps(spec["filters"], default=str, sort_keys=True, indent=1),
					"row_count": len(rows),
					"sha256": digest,
					"status": SNAPSHOT_ORIGINAL,
					"generated_at": payload["generated_at"],
					"app_versions": json.dumps(versions, sort_keys=True),
				}
			)
			snap.flags.erpcore_service = True
			snap.insert(ignore_permissions=True)

			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"{close.name}-r{close.revision}-{frappe.scrub(spec['report'])}-{frappe.scrub(spec['scope'])}.json",
					"is_private": 1,
					"content": content,
					"attached_to_doctype": SNAPSHOT_DOCTYPE,
					"attached_to_name": snap.name,
				}
			)
			file_doc.insert(ignore_permissions=True)
			frappe.db.set_value(SNAPSHOT_DOCTYPE, snap.name, "file", file_doc.file_url, update_modified=False)
			created.append(snap.name)

	return created


def verify(snapshot_name: str) -> dict:
	"""Recompute the hash of a stored snapshot file."""
	snap = frappe.db.get_value(SNAPSHOT_DOCTYPE, snapshot_name, ["file", "sha256"], as_dict=True)
	if not snap or not snap.file:
		return {"ok": False, "reason": "missing"}

	file_name = frappe.db.get_value(
		"File", {"file_url": snap.file, "attached_to_name": snapshot_name}, "name"
	)
	if not file_name:
		return {"ok": False, "reason": "file record missing"}

	content = frappe.get_doc("File", file_name).get_content()
	if isinstance(content, str):
		content = content.encode()
	actual = sha256(content)
	return {"ok": actual == snap.sha256, "expected": snap.sha256, "actual": actual}


def packet_summary(close) -> dict:
	"""Structured summary for the close packet print format."""
	return {
		"tasks": frappe.get_all(
			"Monthly Close Task",
			filters={"monthly_close": close.name},
			fields=[
				"revision",
				"title",
				"status",
				"is_mandatory",
				"manual_certification",
				"completed_by",
				"completed_at",
			],
			order_by="revision asc, idx asc",
		),
		"snapshots": frappe.get_all(
			SNAPSHOT_DOCTYPE,
			filters={"monthly_close": close.name},
			fields=[
				"name",
				"revision",
				"report_name",
				"scope",
				"row_count",
				"sha256",
				"status",
				"generated_at",
			],
			order_by="revision asc, creation asc",
		),
		"waivers": frappe.get_all(
			"Monthly Close Exception",
			filters={"monthly_close": close.name},
			fields=[
				"name",
				"revision",
				"check_label",
				"status",
				"requested_by",
				"decided_by",
				"self_approval_used",
			],
			order_by="creation asc",
		),
		"events": frappe.get_all(
			"Monthly Close Event",
			filters={"monthly_close": close.name},
			fields=["event_time", "revision", "event_type", "actor", "from_state", "to_state"],
			order_by="event_time asc, creation asc",
		),
		"versions": app_versions(),
		"revision": cint(close.revision),
	}
