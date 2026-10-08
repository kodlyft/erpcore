# Monthly Closing

ERP Core's Monthly Close coordinates one company's month end in ERPNext v16: a frozen
checklist, versioned checks, exceptions, preparer → reviewer → manager approval, a hard
close that locks the month through a native **Accounting Period**, and a sealed evidence
packet per revision. It never posts GL, Stock Ledger, Payment Ledger or Period Closing
Voucher entries.

| Guide | For |
| --- | --- |
| [Configuration and cutover](configuration.md) | Administrators setting up a company |
| [Roles and data scope](roles-and-data-scope.md) | Administrators assigning access |
| [Tasks, evidence and bank workpapers](evidence.md) | Preparers and reviewers |
| [Posting coverage](posting-coverage.md) | Controllers, auditors: what the lock does and does not block |
| [Close, reopen and revalidation](close-reopen-revalidation.md) | Close Managers |
| [Job recovery](job-recovery.md) | Close Managers and operators when a background job dies |
| [Packets](packets.md) | Auditors: the sealed record of each revision |
| [Extending checks and vouchers](extending.md) | Developers of other apps |
| [Migration and uninstall](migration-and-uninstall.md) | Operators upgrading or removing the app |
| [Review coverage](review-coverage.md) | Engineering: status of the October 2026 review findings |

## Lifecycle at a glance

```
Draft → In Progress → Ready for Review → Approved → Closing → Closed
                ↑            │                │                  │
                └── Sent Back┘                └── Abort (Closing) │
                                                                 ↓
                          In Progress ← Resume ← Reopened ← Reopen request + approval
```

* **Closing** is the safe intermediate state: the month is already locked, the packet
  is not sealed yet. A failure here never unlocks the month.
* Each reopen starts a new **revision**. Everything from earlier revisions (tasks,
  checks, exceptions, workpapers, snapshots, sealed packets) is kept.
