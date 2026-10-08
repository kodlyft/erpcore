# Copyright (c) 2026, Kodlyft and Contributors
# See license.txt

import frappe

from erpcore.erp_core.workflows import (
	APPROVER_ROLE,
	SUBMITTER_ROLE,
	WORKFLOW_ACTIONS,
	WORKFLOW_NAME,
	WORKFLOW_STATES,
	setup_gate_pass_workflow,
)
from erpcore.tests.utils import ERPCoreTestCase


class TestGatePassWorkflow(ERPCoreTestCase):
	def test_the_workflow_is_installed_and_active(self):
		workflow = frappe.get_doc("Workflow", WORKFLOW_NAME)

		self.assertTrue(workflow.is_active)
		self.assertEqual(workflow.document_type, "Gate Pass")
		self.assertEqual(workflow.workflow_state_field, "workflow_state")

	def test_every_state_and_action_master_exists(self):
		for state in WORKFLOW_STATES:
			self.assertTrue(frappe.db.exists("Workflow State", state), state)

		for action in WORKFLOW_ACTIONS:
			self.assertTrue(frappe.db.exists("Workflow Action Master", action), action)

	def test_states_map_onto_the_status_field(self):
		workflow = frappe.get_doc("Workflow", WORKFLOW_NAME)
		states = {row.state: row for row in workflow.states}

		self.assertEqual(set(states), set(WORKFLOW_STATES))
		self.assertEqual(states["Approved"].doc_status, "1")
		self.assertEqual(states["Draft"].doc_status, "0")
		self.assertTrue(all(row.update_field == "status" for row in workflow.states))
		self.assertEqual(states["Approved"].update_value, "Approved")

	def test_transitions(self):
		workflow = frappe.get_doc("Workflow", WORKFLOW_NAME)
		transitions = {(row.state, row.action): row for row in workflow.transitions}

		self.assertEqual(transitions[("Draft", "Submit for Approval")].next_state, "Pending Approval")
		self.assertEqual(transitions[("Draft", "Submit for Approval")].allowed, SUBMITTER_ROLE)
		self.assertEqual(transitions[("Pending Approval", "Approve")].next_state, "Approved")
		self.assertEqual(transitions[("Pending Approval", "Approve")].allowed, APPROVER_ROLE)
		self.assertEqual(transitions[("Pending Approval", "Reject")].next_state, "Rejected")
		self.assertEqual(transitions[("Rejected", "Submit for Approval")].next_state, "Pending Approval")

	def test_setup_is_idempotent(self):
		setup_gate_pass_workflow()
		workflow = frappe.get_doc("Workflow", WORKFLOW_NAME)

		self.assertEqual(len(workflow.states), len(WORKFLOW_STATES))
		self.assertEqual(len(workflow.transitions), 4)

	def test_disabling_the_setting_deactivates_the_workflow(self):
		with self.change_settings("Gate Pass Settings", enable_approval_workflow=0):
			setup_gate_pass_workflow()
			self.assertFalse(frappe.db.get_value("Workflow", WORKFLOW_NAME, "is_active"))

		setup_gate_pass_workflow()
		self.assertTrue(frappe.db.get_value("Workflow", WORKFLOW_NAME, "is_active"))
