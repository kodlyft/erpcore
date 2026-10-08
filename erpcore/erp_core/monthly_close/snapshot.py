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

Each sealed revision also gets two more snapshot rows:

* "Close Manifest": JSON with everything the revision was closed on — the
  close record, the frozen policy and template, the approved and final check
  runs with every result and finding signature, tasks with actors and
  evidence hashes, exceptions, bank workpapers with their certified hashes,
  reopen requests and the event trail so far, the native lock and its Closed
  Documents, every report snapshot's filters and hash, currency/precision, and
  the app versions with their git commits.
* "Close Packet": a readable HTML packet rendered only from that manifest.

Neither is regenerated later. Reopening marks them Superseded and the next
revision seals its own, so the original packet can always be produced as it
was approved, from `export_revision_packet`. The Monthly Close print format is
the live dashboard view, not the historical record.
"""

import hashlib
import json

import frappe
from frappe import _
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
	"""Installed versions with the git commit of each app, as far as they can be read."""
	from frappe.utils.change_log import get_app_last_commit_ref

	versions = {}
	for app in ("frappe", "erpnext", "erpcore"):
		try:
			version = frappe.get_attr(f"{app}.__version__")
		except Exception:
			version = None
		try:
			commit = get_app_last_commit_ref(app)
		except Exception:
			commit = None
		versions[app] = {"version": version, "commit": commit}
	return versions


def run_report(spec) -> tuple[list, list]:
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
			columns, rows = run_report(spec)
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
			created.append(
				store_snapshot(
					close,
					spec["report"],
					spec["scope"],
					payload_bytes(payload),
					"json",
					versions,
					filters=spec["filters"],
					from_date=spec.get("from_date"),
					to_date=spec.get("to_date"),
					row_count=len(rows),
					generated_at=payload["generated_at"],
				)
			)

	return created


def store_snapshot(
	close,
	report_name: str,
	scope: str,
	content: bytes,
	extension: str,
	versions: dict,
	filters: dict | None = None,
	from_date=None,
	to_date=None,
	row_count: int = 0,
	generated_at=None,
) -> str:
	"""One Snapshot row plus its private file, hashed. Runs inside the seal transaction."""
	snap = frappe.get_doc(
		{
			"doctype": SNAPSHOT_DOCTYPE,
			"monthly_close": close.name,
			"company": close.company,
			"revision": close.revision,
			"report_name": report_name,
			"scope": scope,
			"from_date": from_date,
			"to_date": to_date,
			"filters_json": json.dumps(filters or {}, default=str, sort_keys=True, indent=1),
			"row_count": row_count,
			"sha256": sha256(content),
			"status": SNAPSHOT_ORIGINAL,
			"generated_at": generated_at or now_datetime(),
			"app_versions": json.dumps(versions, sort_keys=True),
		}
	)
	snap.flags.erpcore_service = True
	snap.insert(ignore_permissions=True)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{close.name}-r{close.revision}-{frappe.scrub(report_name)}-{frappe.scrub(scope)}.{extension}",
			"is_private": 1,
			"content": content,
			"attached_to_doctype": SNAPSHOT_DOCTYPE,
			"attached_to_name": snap.name,
		}
	)
	file_doc.insert(ignore_permissions=True)
	frappe.db.set_value(SNAPSHOT_DOCTYPE, snap.name, "file", file_doc.file_url, update_modified=False)
	return snap.name


MANIFEST = "Close Manifest"
PACKET = "Close Packet"


def record_rows(doctype: str, filters: dict, order_by: str = "creation asc") -> list[dict]:
	return [
		{k: v for k, v in row.items() if not k.startswith("_")}
		for row in frappe.get_all(doctype, filters=filters, fields=["*"], order_by=order_by)
	]


def check_run_record(run_name: str | None) -> dict | None:
	if not run_name or not frappe.db.exists("Monthly Close Check Run", run_name):
		return None
	run = frappe.get_doc("Monthly Close Check Run", run_name).as_dict(no_default_fields=True)
	run["name"] = run_name
	run["results"] = [
		{k: v for k, v in row.items() if k not in ("parent", "parentfield", "parenttype", "doctype")}
		for row in run.get("results") or []
	]
	return run


def build_manifest(close, snapshot_names: list[str]) -> dict:
	"""Everything this revision was closed on, from the locked month, in one structure."""
	import erpnext

	from erpcore.erp_core.monthly_close import native_lock

	revision = {"monthly_close": close.name, "revision": close.revision}
	period = None
	if close.accounting_period and frappe.db.exists("Accounting Period", close.accounting_period):
		ap = frappe.get_doc("Accounting Period", close.accounting_period)
		period = {
			"name": ap.name,
			"period_name": ap.period_name,
			"start_date": ap.start_date,
			"end_date": ap.end_date,
			"disabled": ap.disabled,
			"exempted_role": ap.exempted_role,
			"owned": bool(close.lock_owned),
			"closed_documents": sorted((row.document_type, cint(row.closed)) for row in ap.closed_documents),
		}

	currency = erpnext.get_company_currency(close.company)
	record = close.as_dict(no_default_fields=True)
	record["name"] = close.name
	record.pop("dashboard_html", None)
	return {
		"manifest_version": 1,
		"sealed_at": now_datetime(),
		"close": record,
		"policy": json.loads(close.policy_snapshot or "{}"),
		"template": json.loads(close.template_snapshot or "{}"),
		"currency": currency,
		"precision": cint(frappe.get_precision("GL Entry", "debit", currency=currency)) or 2,
		"approved_check_run": check_run_record(close.approved_check_run),
		"final_check_run": check_run_record(close.closing_check_run),
		"tasks": record_rows("Monthly Close Task", revision, "idx asc, creation asc"),
		"exceptions": record_rows("Monthly Close Exception", revision),
		"bank_certifications": record_rows("Monthly Close Bank Certification", revision),
		"reopen_requests": record_rows("Monthly Close Reopen Request", {"monthly_close": close.name}),
		"events": record_rows(
			"Monthly Close Event", {"monthly_close": close.name}, "event_time asc, creation asc"
		),
		"lock": {"accounting_period": period, "health": native_lock.lock_health(close)},
		"reports": [
			frappe.db.get_value(
				SNAPSHOT_DOCTYPE,
				name,
				[
					"name",
					"report_name",
					"scope",
					"from_date",
					"to_date",
					"filters_json",
					"row_count",
					"sha256",
					"file",
				],
				as_dict=True,
			)
			for name in snapshot_names
		],
		"app_versions": app_versions(),
	}


def seal_revision_packet(close, snapshot_names: list[str]) -> dict:
	"""Store the revision manifest and its readable packet. Returns their snapshot names."""
	versions = app_versions()
	manifest = build_manifest(close, snapshot_names)
	manifest_bytes = payload_bytes(manifest)
	manifest_name = store_snapshot(
		close, MANIFEST, f"Revision {close.revision}", manifest_bytes, "json", versions
	)

	from frappe.utils.jinja import get_jenv

	html = (
		get_jenv()
		.get_template("erpcore/templates/monthly_close/packet.html")
		.render(m=frappe._dict(json.loads(manifest_bytes)), manifest_sha=sha256(manifest_bytes))
	)
	packet_name = store_snapshot(close, PACKET, f"Revision {close.revision}", html.encode(), "html", versions)
	return {"manifest": manifest_name, "packet": packet_name}


def revision_packet(close_name: str, revision: int) -> dict:
	"""The sealed packet of one revision: file URLs and hashes, verified. Never regenerated."""
	result = {}
	for kind in (MANIFEST, PACKET):
		name = frappe.db.get_value(
			SNAPSHOT_DOCTYPE,
			{"monthly_close": close_name, "revision": cint(revision), "report_name": kind},
			"name",
		)
		if not name:
			continue
		row = frappe.db.get_value(SNAPSHOT_DOCTYPE, name, ["name", "file", "sha256", "status"], as_dict=True)
		row["verified"] = verify(name)["ok"]
		result[kind] = row
	return result


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
