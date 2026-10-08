# Job recovery

Each hard close stage stores: pending action, fencing token, RQ job id, attempt number,
requested-at and started-at. Every step re-checks the token under the close row lock,
so a worker whose token was replaced (retry, recovery, abort) writes nothing.

## When is a stage dead?

Only when **both** hold:

* it has been pending longer than the lease (job timeout + 10 minutes), and
* its RQ job is neither queued nor running (`is_job_enqueued`). If Redis cannot be
  reached, the stage is *not* presumed dead.

## What recovery does (hourly, `recover_stalled_closes`, and on demand)

| Stage died in | State | Recovery | Lock |
| --- | --- | --- | --- |
| Hard close before or during the lock transaction | Approved | pending cleared, `last_error` recorded; request Hard Close again | none was committed |
| Seal (after the lock committed) | Closing | becomes **Seal Failed**; Retry Sealing or Abort Close | stays in force |

Nothing ever unlocks on a timeout. A manager can also press **Hard Close** or **Retry
Sealing** again once a stage is dead; while it may still be running these are refused.

Check runs have their own lease: a Running run whose lease expired can be claimed by a
new worker (new token; the old worker's results are discarded), and
`recover_stuck_runs` (hourly) marks expired or long-queued runs Failed.

## Authorization at execution time

A queued hard close re-checks, when it runs, that the module and the company's policy
are enabled and that the requester is still an enabled Close Manager (or System
Manager) with access to the company. If not, the hard close is refused (nothing
locked); a seal is refused but the lock stays, for a manager to decide.

## Files

Report snapshots, the manifest and the packet are created inside the seal
transaction. If it fails, the database rows roll back; files written to disk by that
attempt may remain as orphans without File records. A retry writes new files.
