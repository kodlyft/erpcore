# Tasks, evidence and bank workpapers

## Checklist tasks

* Task definitions (title, mandatory, evidence required, dependencies, **assigned
  role**) come from the frozen template and cannot be edited.
* **Who may work on a task:** the assigned user; else holders of the assigned role;
  else Close Preparers. Close Managers always. The check uses the assignment *as
  stored*, so nobody can assign a task to themselves and complete it in the same save;
  reassignment needs a Close Manager.
* **Completion identity is server-stamped.** `completed_by`/`completed_at` are set when
  the status changes to Done and are kept afterwards; values sent by a client are
  discarded.
* Every task edit first locks the parent close row. A late edit therefore waits for a
  concurrent review/approval and is then refused because the checklist is frozen.

## Evidence files

Evidence (task evidence, bank statements, exception evidence) must resolve to an
existing **private File** that the user can read and that is attached to the record
itself, to its Monthly Close, or to another monthly close record of the same company
(shared evidence). Its SHA-256 is stored next to the URL.

After that:

* Deleting the file, making it public or changing its URL through Frappe is refused
  (the refusal happens before Frappe touches the file on disk).
* If the bytes or File record change behind the API anyway, the close notices:
  the task stops counting as complete, the bank check reports the statement, and an
  exception whose evidence changed no longer covers its finding.
* Replacing evidence on a Done task requires attaching it again.

## Bank workpapers

* A workpaper's company, close, revision, bank account and GL account are fixed when it
  is created.
* Amounts are in the **bank account's currency** at that currency's precision. The
  policy tolerance (company currency) applies only to company-currency accounts; any
  unexplained difference on a foreign-currency account is reported.
* The statement should be dated on the last day of the month. A statement within 31
  days of month end can be certified only with a **Bridging to Month End** note;
  further away it is refused.
* Certification (by someone other than the preparer) records a hash of everything
  certified. If any certified value, the statement file or the ledger balance changes
  afterwards, the bank check reports it.
