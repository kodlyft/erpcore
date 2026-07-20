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

# before_uninstall = "erpcore.uninstall.before_uninstall"
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

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

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
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"erpcore.erp_core.tasks.reconcile_cheque_leaf_status",
		"erpcore.erp_core.tasks.notify_pdc_due",
		"erpcore.erp_core.tasks.update_gate_pass_overdue_status",
		"erpcore.erp_core.tasks.expire_stale_visitor_passes",
	],
	"daily_long": [
		"erpcore.erp_core.tasks.notify_overdue_gate_pass_returns",
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
