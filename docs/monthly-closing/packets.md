# Packets

Every sealed revision has, as **Monthly Close Snapshot** rows with private files and
SHA-256 hashes:

* report snapshots – Trial Balance (month), Profit and Loss (month and year to date),
  Balance Sheet (as at month end), AR/AP summaries (month-end ageing), Stock and Account
  Value Comparison (perpetual inventory) – produced by ERPNext's own report code with
  the filters stored next to them;
* **Close Manifest** – JSON: the close record, frozen policy and template, the approved
  and final check runs with every result and finding signature, tasks with actors and
  evidence hashes, exceptions, bank workpapers with certified hashes, reopen requests,
  the event trail, the Accounting Period with its Closed Documents and health, every
  report snapshot's filters and hash, currency and precision, and the frappe/ERPNext/
  erpcore versions with git commits;
* **Close Packet** – readable HTML rendered **only from the manifest**, with the Jinja
  template `erpcore/templates/monthly_close/packet.html`. Changing that template affects
  packets sealed afterwards; packets already sealed are stored files and never re-rendered.

They are never regenerated. **Sealed Packet** on the form (or
`api.export_revision_packet(name, revision)`) opens the packet of any revision and
verifies its hash. Reopening marks a revision's snapshots Superseded; it never
overwrites or deletes them.

The **Live Packet View** print format reads current records across all revisions; it
is a dashboard, not the historical record.

The hashes detect accidental or casual alteration. They are not legal digital
signatures and do not protect against someone with database and file-system access.
