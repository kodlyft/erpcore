# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

LEAF_UNUSED = "Unused"
LEAF_RESERVED = "Reserved"
LEAF_ISSUED = "Issued"
LEAF_CLEARED = "Cleared"
LEAF_BOUNCED = "Bounced"
LEAF_STOPPED = "Stopped"
LEAF_VOID = "Void"
LEAF_LOST = "Lost"

LEAF_STATUSES = (
	LEAF_UNUSED,
	LEAF_RESERVED,
	LEAF_ISSUED,
	LEAF_CLEARED,
	LEAF_BOUNCED,
	LEAF_STOPPED,
	LEAF_VOID,
	LEAF_LOST,
)

LEAF_AVAILABLE_STATUSES = (LEAF_UNUSED, LEAF_RESERVED)

LEAF_CONSUMED_STATUSES = (LEAF_ISSUED, LEAF_CLEARED, LEAF_BOUNCED, LEAF_STOPPED)

RECEIPT_DRAFT = "Draft"
RECEIPT_RECEIVED = "Received"
RECEIPT_DEPOSITED = "Deposited"
RECEIPT_CLEARED = "Cleared"
RECEIPT_BOUNCED = "Bounced"
RECEIPT_RETURNED = "Returned"
RECEIPT_REPLACED = "Replaced"
RECEIPT_CANCELLED = "Cancelled"

VOID_REASON_CANCELLED_VOUCHER = "Cancelled Voucher"

DEFAULT_VOID_REASONS = (
	("Spoiled / Misprint", "Outward", "Cheque damaged or misprinted during printing."),
	("Signature Error", "Outward", "Incorrect or missing authorised signature."),
	("Wrong Amount", "Both", "Amount written does not match the intended payment."),
	("Wrong Payee", "Both", "Cheque made out to the wrong party."),
	("Lost in Transit", "Both", "Cheque lost after being handed over."),
	("Stopped by Drawer", "Both", "Payment stopped by the account holder."),
	("Bank Returned", "Inward", "Returned unpaid by the bank."),
	("Insufficient Funds", "Inward", "Returned for want of funds."),
	(
		VOID_REASON_CANCELLED_VOUCHER,
		"Outward",
		"The Payment Entry or Journal Entry using this cheque was cancelled.",
	),
	("Test Print", "Outward", "Consumed while aligning the cheque printer."),
)

VOUCHER_DOCTYPES = ("Payment Entry", "Journal Entry")

VOUCHER_FIELDS = {
	"Payment Entry": {"number": "reference_no", "date": "reference_date"},
	"Journal Entry": {"number": "cheque_no", "date": "cheque_date"},
}
