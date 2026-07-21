# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class GatePassSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpcore.erp_core.doctype.gate_pass_notify_role.gate_pass_notify_role import GatePassNotifyRole

		allow_exit_without_approval: DF.Check
		allow_purchase_receipt_creation: DF.Check
		default_return_days: DF.Int
		enable_approval_workflow: DF.Check
		enable_blacklist_check: DF.Check
		notify_roles: DF.TableMultiSelect[GatePassNotifyRole]
		overdue_alert_days: DF.Int
		require_guard_verification: DF.Check
		require_visitor_id_proof: DF.Check
		visitor_badge_prefix: DF.Data | None
	# end: auto-generated types

	pass
