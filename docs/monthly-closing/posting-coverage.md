# Posting coverage

The **Monthly Close Posting Coverage** report lists every known write path with its
native control, erpcore control, status and the regression test that proves it.

## How a locked month is protected

1. **Native Accounting Period** owned by the close: refuses saving/submitting/cancelling
   the closing doctypes dated in the month, and GL postings for those doctypes.
2. **erpcore ledger gate** (`posting_guard`), `before_submit` on GL Entry, Stock Ledger
   Entry, Payment Ledger Entry and Advance Payment Ledger Entry. It takes a shared lock
   on the month's close row, so hard close waits for in-flight postings and later
   postings wait for hard close and then see the locked state.
3. **Repost guards** on `validate` of Repost Item Valuation, Repost Accounting Ledger and
   Repost Payment Ledger, because reposts rewrite existing rows in place
   (`update_sle_valuation_fields`) without submitting new ledger rows.
4. **Cumulative stock effects:** a stock posting in an earlier open month that would
   revalue later SLEs of the same item and warehouse in a locked month is refused.

## Deliberately allowed

* **Later settlement.** A payment dated after the closed month can be reconciled with
  an invoice of the closed month: ERPNext dates the allocation on or after the payment
  date, so it lands in an open month. The live AR dashboard changes; the sealed packet
  does not.
* Bank clearance dates, cancellations with immutable ledger (reversal dated today).

## Earlier-month postings and later locked months

A GL, Stock Ledger or Payment Ledger row dated before a locked month changes that
month's balances brought forward. Such a posting is **refused unless every later locked
month is already flagged for revalidation**. This applies equally to months before the
cutover (never managed) and to open managed months.

* The controlled way in is to reopen the earliest closed month after the posting date:
  reopening flags every later Closed month (their locks stay), and the posting is then
  allowed. Revalidation later compares the sealed balances, including balances brought
  forward, and refuses if they changed. See
  [close-reopen-revalidation.md](close-reopen-revalidation.md).
* A month can be hard-closed only when every earlier managed month is Closed and
  revalidated, so a month is never closed on top of an open earlier month.
* Stock postings that would revalue a later locked month's own stock movement (same
  item and warehouse) stay refused even when that month is flagged.

## Not covered

Direct SQL, bench console and privileged administrator edits. Fingerprints, evidence
hashes and the daily lock integrity check may detect some of these afterwards.

## Fingerprint

The close fingerprint hashes, exactly (no fixed rounding): GL movement by account,
finance book, cost center, project, accounting dimensions and account currency; GL
balances brought forward; SLE movement and opening by item and warehouse; Payment
Ledger outstanding per party **and invoice** with due date; every draft voucher's
identity, amount, workflow state and modification time; unfinished reposts; unposted
depreciation; bank workpaper hashes; task evidence hashes; and the frozen policy. The
form dashboard caches its freshness for two minutes; every transition recomputes it.
