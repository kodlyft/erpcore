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

GL and Payment Ledger postings in an open month change only the *opening* balances of
later months. Because months close in order after the cutover, this happens only after
an earlier month was reopened. Reopening flags every later Closed month for
revalidation (its lock stays), and revalidation compares the sealed balances,
including balances brought forward. See
[close-reopen-revalidation.md](close-reopen-revalidation.md).

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
