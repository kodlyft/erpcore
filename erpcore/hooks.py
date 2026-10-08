app_name = "erpcore"
app_title = "ERP Core"
app_publisher = "Kodlyft"
app_description = "Core requirements for ERPNext"
app_email = "hello@kodlyft.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "erpcore",
# 		"logo": "/assets/erpcore/logo.png",
# 		"title": "ERP Core",
# 		"route": "/erpcore",
# 		"has_permission": "erpcore.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/erpcore/css/erpcore.css"
app_include_js = "/assets/erpcore/js/cheque_common.js"

# include js, css files in header of web template
# web_include_css = "/assets/erpcore/css/erpcore.css"
# web_include_js = "/assets/erpcore/js/erpcore.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "erpcore/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Payment Entry": "public/js/payment_entry.js",
	"Journal Entry": "public/js/journal_entry.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "erpcore/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "erpcore.utils.jinja_methods",
# 	"filters": "erpcore.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "erpcore.install.before_install"
after_install = "erpcore.install.after_install"
after_migrate = "erpcore.setup.after_migrate"

# Uninstallation
# ------------

before_uninstall = "erpcore.uninstall.before_uninstall"
# after_uninstall = "erpcore.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "erpcore.utils.before_app_install"
# after_app_install = "erpcore.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "erpcore.utils.before_app_uninstall"
# after_app_uninstall = "erpcore.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "erpcore.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "erpcore.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

MC_PERMISSIONS = "erpcore.erp_core.monthly_close.permissions"

permission_query_conditions = {
	"Gate Pass": "erpcore.erp_core.gate_pass_base.gate_pass_query_conditions",
	"Visitor Gate Pass": "erpcore.erp_core.gate_pass_base.visitor_gate_pass_query_conditions",
	"Monthly Close": f"{MC_PERMISSIONS}.monthly_close_conditions",
	"Monthly Close Policy": f"{MC_PERMISSIONS}.policy_conditions",
	"Monthly Close Task": f"{MC_PERMISSIONS}.task_conditions",
	"Monthly Close Check Run": f"{MC_PERMISSIONS}.check_run_conditions",
	"Monthly Close Exception": f"{MC_PERMISSIONS}.exception_conditions",
	"Monthly Close Bank Certification": f"{MC_PERMISSIONS}.bank_cert_conditions",
	"Monthly Close Reopen Request": f"{MC_PERMISSIONS}.reopen_conditions",
	"Monthly Close Event": f"{MC_PERMISSIONS}.event_conditions",
	"Monthly Close Snapshot": f"{MC_PERMISSIONS}.snapshot_conditions",
}

has_permission = {
	doctype: f"{MC_PERMISSIONS}.has_permission"
	for doctype in (
		"Monthly Close",
		"Monthly Close Policy",
		"Monthly Close Task",
		"Monthly Close Check Run",
		"Monthly Close Exception",
		"Monthly Close Bank Certification",
		"Monthly Close Reopen Request",
		"Monthly Close Event",
		"Monthly Close Snapshot",
	)
}

has_permission["File"] = "erpcore.erp_core.monthly_close.evidence.file_has_permission"

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Payment Entry": {
		"validate": "erpcore.erp_core.cheque_utils.voucher_validate",
		"on_submit": "erpcore.erp_core.cheque_utils.voucher_on_submit",
		"on_cancel": "erpcore.erp_core.cheque_utils.voucher_on_cancel",
		"on_trash": "erpcore.erp_core.cheque_utils.release_reservation",
		"on_change": "erpcore.erp_core.cheque_utils.sync_leaf_from_voucher",
	},
	"Journal Entry": {
		"validate": "erpcore.erp_core.cheque_utils.voucher_validate",
		"on_submit": "erpcore.erp_core.cheque_utils.voucher_on_submit",
		"on_cancel": "erpcore.erp_core.cheque_utils.voucher_on_cancel",
		"on_trash": "erpcore.erp_core.cheque_utils.release_reservation",
		"on_change": "erpcore.erp_core.cheque_utils.sync_leaf_from_voucher",
	},
	# Monthly close: serialization gate on every ledger row (see monthly_close/posting_guard.py).
	"GL Entry": {
		"before_submit": "erpcore.erp_core.monthly_close.posting_guard.guard_ledger_entry",
	},
	"Stock Ledger Entry": {
		"before_submit": "erpcore.erp_core.monthly_close.posting_guard.guard_ledger_entry",
	},
	"Payment Ledger Entry": {
		"before_submit": "erpcore.erp_core.monthly_close.posting_guard.guard_ledger_entry",
	},
	"Advance Payment Ledger Entry": {
		"before_submit": "erpcore.erp_core.monthly_close.posting_guard.guard_advance_ledger_entry",
	},
	"Repost Item Valuation": {
		"validate": "erpcore.erp_core.monthly_close.posting_guard.guard_repost",
	},
	"Repost Accounting Ledger": {
		"validate": "erpcore.erp_core.monthly_close.posting_guard.guard_repost",
	},
	"Repost Payment Ledger": {
		"validate": "erpcore.erp_core.monthly_close.posting_guard.guard_repost",
	},
	"File": {
		"before_validate": "erpcore.erp_core.monthly_close.evidence.protect_evidence_file",
		"on_trash": "erpcore.erp_core.monthly_close.evidence.protect_evidence_file_delete",
	},
	"Accounting Period": {
		"validate": "erpcore.erp_core.monthly_close.native_lock.protect_owned_period",
		"on_trash": "erpcore.erp_core.monthly_close.native_lock.protect_owned_period_delete",
	},
}

# Registered close checks from other apps: dotted module paths that call register_check on import.
erpcore_monthly_close_checks = []

# Extra draft voucher types for the close's draft check and fingerprint:
# [{"doctype": "My Voucher", "date_field": "posting_date", "amount_field": "base_grand_total"}].
# Every doctype in ERPNext's `period_closing_doctypes` hook is included automatically.
erpcore_monthly_close_draft_vouchers = []

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"erpcore.erp_core.tasks.reconcile_cheque_leaf_status",
		"erpcore.erp_core.tasks.notify_pdc_due",
		"erpcore.erp_core.tasks.update_gate_pass_overdue_status",
		"erpcore.erp_core.tasks.expire_stale_visitor_passes",
		"erpcore.erp_core.monthly_close.scheduled.check_lock_integrity",
		"erpcore.erp_core.monthly_close.scheduled.send_task_reminders",
	],
	"daily_long": [
		"erpcore.erp_core.tasks.notify_overdue_gate_pass_returns",
	],
	"hourly": [
		"erpcore.erp_core.monthly_close.scheduled.recover_stuck_runs",
		"erpcore.erp_core.monthly_close.scheduled.recover_stalled_closes",
	],
}

# Testing
# -------

# before_tests = "erpcore.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "erpcore.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "erpcore.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "erpcore.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["erpcore.utils.before_request"]
# after_request = ["erpcore.utils.after_request"]

# Job Events
# ----------
# before_job = ["erpcore.utils.before_job"]
# after_job = ["erpcore.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"erpcore.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
