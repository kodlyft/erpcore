# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

"""Names shared by the monthly close controllers, services, reports and tests."""

CLOSE_DOCTYPE = "Monthly Close"
POLICY_DOCTYPE = "Monthly Close Policy"
SETTINGS_DOCTYPE = "Monthly Close Settings"
TEMPLATE_DOCTYPE = "Monthly Close Template"
TASK_DOCTYPE = "Monthly Close Task"
CHECK_RUN_DOCTYPE = "Monthly Close Check Run"
EXCEPTION_DOCTYPE = "Monthly Close Exception"
BANK_CERT_DOCTYPE = "Monthly Close Bank Certification"
REOPEN_DOCTYPE = "Monthly Close Reopen Request"
EVENT_DOCTYPE = "Monthly Close Event"
SNAPSHOT_DOCTYPE = "Monthly Close Snapshot"

# Every company-scoped record of the module. Used for permission hooks.
COMPANY_SCOPED_DOCTYPES = (
	CLOSE_DOCTYPE,
	POLICY_DOCTYPE,
	TASK_DOCTYPE,
	CHECK_RUN_DOCTYPE,
	EXCEPTION_DOCTYPE,
	BANK_CERT_DOCTYPE,
	REOPEN_DOCTYPE,
	EVENT_DOCTYPE,
	SNAPSHOT_DOCTYPE,
)

# Lifecycle states. `Closing` is the sealed intermediate state: the native lock
# is active and postings are blocked, but the evidence packet is not sealed yet.
DRAFT = "Draft"
IN_PROGRESS = "In Progress"
READY_FOR_REVIEW = "Ready for Review"
APPROVED = "Approved"
CLOSING = "Closing"
CLOSED = "Closed"
REOPENED = "Reopened"

STATES = (DRAFT, IN_PROGRESS, READY_FOR_REVIEW, APPROVED, CLOSING, CLOSED, REOPENED)

# States in which postings dated inside the month are refused by the posting guard.
LOCKED_STATES = (CLOSING, CLOSED)

# States in which preparers may still change tasks, certifications and evidence.
EDITABLE_STATES = (DRAFT, IN_PROGRESS, REOPENED)

ROLE_PREPARER = "Close Preparer"
ROLE_REVIEWER = "Close Reviewer"
ROLE_MANAGER = "Close Manager"
ROLE_AUDITOR = "Close Auditor"
CLOSE_ROLES = (ROLE_PREPARER, ROLE_REVIEWER, ROLE_MANAGER, ROLE_AUDITOR)

# Check result statuses.
PASSED = "Passed"
WARNING = "Warning"
BLOCKER = "Blocker"
NOT_APPLICABLE = "Not Applicable"
ERROR = "Error"

# Check run statuses.
RUN_QUEUED = "Queued"
RUN_RUNNING = "Running"
RUN_COMPLETED = "Completed"
RUN_FAILED = "Failed"
RUN_STALE = "Stale"

# Waiver / reopen request statuses.
REQUESTED = "Requested"
REQUEST_APPROVED = "Approved"
REQUEST_REJECTED = "Rejected"
SUPERSEDED = "Superseded"

# Snapshot statuses.
SNAPSHOT_ORIGINAL = "Original"
SNAPSHOT_SUPERSEDED = "Superseded"

# Custom field on Accounting Period that records which close owns it.
OWNER_FIELD = "erpcore_monthly_close"

# Flag names. Set only by service code around writes it is entitled to make.
TRANSITION_FLAG = "erpcore_close_transition"
LOCK_SERVICE_FLAG = "erpcore_close_lock_service"

DEFAULT_TEMPLATE = "Standard Monthly Close"
