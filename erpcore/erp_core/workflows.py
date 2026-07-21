# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

import frappe

WORKFLOW_NAME = "Gate Pass Approval"
DOCTYPE = "Gate Pass"

WORKFLOW_STATES = {
	"Draft": "",
	"Pending Approval": "Warning",
	"Approved": "Success",
	"Rejected": "Danger",
}

WORKFLOW_ACTIONS = ("Submit for Approval", "Approve", "Reject")

SUBMITTER_ROLE = "Gate Pass User"
APPROVER_ROLE = "Gate Pass Manager"


def setup_gate_pass_workflow():
	"""Install, update or disable the workflow to match the current setting."""
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	enabled = frappe.db.get_single_value("Gate Pass Settings", "enable_approval_workflow")

	if not enabled:
		_disable_workflow()
		return

	_ensure_masters()
	_upsert_workflow()


def _disable_workflow():
	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		frappe.db.set_value("Workflow", WORKFLOW_NAME, "is_active", 0)


def _ensure_masters():
	for state, style in WORKFLOW_STATES.items():
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)

	for action in WORKFLOW_ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)


def _states():
	rows = [
		("Draft", "0", SUBMITTER_ROLE, "Draft"),
		("Pending Approval", "0", APPROVER_ROLE, "Pending Approval"),
		("Approved", "1", APPROVER_ROLE, "Approved"),
		("Rejected", "0", SUBMITTER_ROLE, "Rejected"),
	]

	return [
		{
			"state": state,
			"doc_status": doc_status,
			"allow_edit": allow_edit,
			"update_field": "status",
			"update_value": value,
		}
		for state, doc_status, allow_edit, value in rows
	]


def _transitions():
	return [
		{
			"state": "Draft",
			"action": "Submit for Approval",
			"next_state": "Pending Approval",
			"allowed": SUBMITTER_ROLE,
		},
		{
			"state": "Pending Approval",
			"action": "Approve",
			"next_state": "Approved",
			"allowed": APPROVER_ROLE,
		},
		{
			"state": "Pending Approval",
			"action": "Reject",
			"next_state": "Rejected",
			"allowed": APPROVER_ROLE,
		},
		{
			"state": "Rejected",
			"action": "Submit for Approval",
			"next_state": "Pending Approval",
			"allowed": SUBMITTER_ROLE,
		},
	]


def _upsert_workflow():
	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		workflow = frappe.get_doc("Workflow", WORKFLOW_NAME)
		workflow.states = []
		workflow.transitions = []
	else:
		workflow = frappe.new_doc("Workflow")
		workflow.workflow_name = WORKFLOW_NAME

	workflow.document_type = DOCTYPE
	workflow.workflow_state_field = "workflow_state"
	workflow.is_active = 1
	workflow.send_email_alert = 0
	workflow.override_status = 0

	for state in _states():
		workflow.append("states", state)

	for transition in _transitions():
		workflow.append("transitions", transition)

	workflow.save(ignore_permissions=True)
