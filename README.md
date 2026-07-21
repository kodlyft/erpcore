<div align="center">

<img src="erpcore/public/images/erpcore-logo.svg" alt="ERP Core" width="96" height="96">

# ERP Core

**Core requirements for [ERPNext](https://erpnext.com), built on the [Frappe Framework](https://frappeframework.com).**

[![CI](https://github.com/kodlyft/erpcore/actions/workflows/ci.yml/badge.svg)](https://github.com/kodlyft/erpcore/actions/workflows/ci.yml)
[![Linters](https://github.com/kodlyft/erpcore/actions/workflows/linter.yml/badge.svg)](https://github.com/kodlyft/erpcore/actions/workflows/linter.yml)
[![Release](https://github.com/kodlyft/erpcore/actions/workflows/release.yml/badge.svg)](https://github.com/kodlyft/erpcore/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://conventionalcommits.org)

</div>

---

## Overview

**ERP Core** packages shared, cross-cutting requirements used across Kodlyft's ERPNext
deployments, so they live in one versioned place instead of being duplicated per site.

It currently ships two modules:

| Module | What it does |
| ------ | ------------ |
| **Cheque Management** | Cheque books, per-leaf status tracking, void reasons, and post-dated cheques received from customers |
| **Gate Pass** | Inward/outward material passes, returnable-material tracking, and security check-in/check-out |

> **Status: active development.** Cheque books and gate passes are functional. Inward
> cheque receipts, cheque printing, visitor passes, print formats and reports are still
> landing. Expect the surface area to change until a `1.0` release. Watch the
> [releases](https://github.com/kodlyft/erpcore/releases) page.

---

## Cheque Management

ERPNext gives you a free-text `Cheque/Reference No` on Payment Entry and a
`Reference Number` on Journal Entry. There is no cheque book, no per-cheque status, no
void register, and no way to answer "which cheque numbers are still unused?"

### Cheque books and leaves

A **Cheque Book** is a bank account plus a contiguous range of pre-printed numbers. Submitting one generates a **Cheque Leaf**
document per number, each with its own status.

```
Cheque Book  CHQ-BK-2026-0001
  bank_account    HBL Current
  prefix "A"  start 100101  ×25 leaves
       │
       └─▶ 25 Cheque Leaf records:  A100101 … A100125
```

Overlapping books are rejected: if a leaf in `[start, end]` already exists on the same
bank account, submission throws and names the conflicting book. This catches the common
real-world error of entering the same book twice.

Books above the configured threshold generate their leaves in a background job, which is
resumable. A retry after a worker timeout tops the book up rather than duplicating it.

### Leaf lifecycle

```
Unused ──▶ Reserved ──▶ Issued ──▶ Cleared
   │           │           │
   │           │           ├──▶ Stopped   (stop payment)
   │           │           └──▶ Bounced   (returned unpaid)
   ├──▶ Void   (misprint, wrong payee, … - reason required)
   └──▶ Lost
```

- **Reserved** is set when a *draft* voucher selects a leaf, so two users cannot both
  draft against the same cheque and discover the clash only at submit.
- **Void requires a reason code** Reasons are a master
  (`Cheque Void Reason`), not a hardcoded list, because they are reported on and differ
  per client.
- **Cancelling a voucher voids its leaf** with reason *Cancelled Voucher* rather than
  returning it to the pool. A cheque physically handed over is gone regardless of what
  happens to the entry, and the number must stay traceable. Sites that genuinely re-run
  misprinted stationery can flip `Allow Void Leaf Reuse` in Cheque Settings.

### Linking to vouchers

Selecting a **Cheque Leaf** on a Payment Entry or Journal Entry fills in the existing
`reference_no` / `cheque_no` and date fields.

The existing Data fields are deliberately left alone rather than converted to Links.
They are mandatory for *every* bank transaction, wire transfers, IBFT, online payments,
not just cheques, so a Link would make non-cheque payments impossible to record. (Frappe's
`ALLOWED_FIELDTYPE_CHANGE` forbids `Data → Link` in any case.)

Three independent layers stop two vouchers consuming one leaf:

1. the link query never offers a leaf that is not available,
2. a controller guard throws on save, and
3. a **unique index** on `Cheque Leaf(reference_doctype, reference_name)`.

Only the third survives a genuine concurrent double-submit, which is why it exists.

### Clearance sync

ERPNext writes `clearance_date` from three places, and only one of them runs hooks:

| Source | Mechanism | Fires `on_change`? |
| ------ | --------- | ------------------ |
| Bank Clearance | `Document.db_set` | **Yes** |
| Bank Reconciliation Tool | raw `frappe.db.set_value` | No |
| Bank Transaction | raw `frappe.db.set_value` | No |

So a document hook alone would leave cheques cleared through reconciliation stuck on
*Issued* forever. A daily scheduled reconciler is the authoritative sync; the hook is
just the fast path.

### Inward cheques

**Cheque Receipt** records customer cheques, including post-dated ones, through
`Received → Deposited → Cleared / Bounced / Returned`.

These are registers only **no GL entries are posted**. The Payment Entry you create on
clearing does all the accounting, which keeps the cheque register from ever
double-counting against the ledger.

---

## Gate Pass

Records material physically crossing a site boundary. A gate pass is a *security*
document, not a stock document: Purchase Receipt, Delivery Note and Stock Entry keep
owning the ledger.

### Pass types

Direction plus returnability derives the type, and the naming series follows it. So the
document number itself tells you what kind of pass it is.

| Direction | Returnable | Type | Series |
| --------- | ---------- | ---- | ------ |
| Inward | No | IGP | `GP-IGP-.YYYY.-.#####` |
| Outward | No | NRGP | `GP-NRGP-.YYYY.-.#####` |
| Either | Yes | RGP | `GP-RGP-.YYYY.-.#####` |

Items can be pulled from a **Purchase Order**, **Delivery Note** or **Stock Entry**, and
pulling from a second document appends rather than replacing what you already collected.

### Lifecycle

```
Draft ─▶ Pending Approval ─▶ Approved        (Frappe Workflow, optional)
                               │
                    guard verifies ─▶ At Gate
                               │
                     check in / out
                               │
              ┌────────────────┴─────────────────┐
        non-returnable                      returnable
              │                                  │
     Exited (outward)                    Exited ─▶ Partly Returned ─▶ Returned
     Received (inward)                       └──▶ Overdue
```

The approval workflow deliberately **stops at Approved**. Everything after that is an
operational event a guard records on an already-submitted document, and Frappe Workflow
cannot stamp a timestamp or a user for you, so those are one-tap whitelisted methods
instead. The workflow writes into the same `status` field, so there is no
`workflow_state`/`status` pair to keep in sync.

### Returnable material

Returns are a separate **Gate Pass Return** document. The same idiom as Purchase Order →
Purchase Receipt. Each return carries its own date, gate, guard and condition per line,
which mutating quantities on the original pass could never record.

Returning more than is still outside is refused, and the outstanding calculation excludes
the return's own prior version so amending cannot double-count.

### Security

`Gate Keeper` gets **read** access only. Check-in, check-out and verification go through
whitelisted methods that write a fixed allow-list of fields, so a guard can stamp a
timestamp on a submitted pass but can never alter quantities, parties or items.

Vehicles and drivers link to ERPNext's existing `Vehicle` and `Driver` masters rather
than being free text.

---

## Requirements

| Dependency       | Version         |
| ---------------- | --------------- |
| Frappe Framework | v16             |
| ERPNext          | v16 (required)  |
| Python           | >= 3.14         |

ERPNext is a hard dependency. It is declared in `required_apps`, so installing
`erpcore` on a site without ERPNext will fail.

> **Do not install alongside [`slw`](https://github.com/kodlyft/slw).** Both apps define
> a `Gate Pass` doctype and will collide. ERP Core's implementation supersedes it.

## Installation

Install with the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/kodlyft/erpcore --branch develop
bench --site your-site.com install-app erpcore
```

Installation seeds the default void reason codes and creates the database indexes. Both
steps are idempotent and re-run on every `bench migrate`, so a site installed before a
seed was added still picks it up.

## Configuration

| Setting | Where | Notes |
| ------- | ----- | ----- |
| Padding, book size limits, void reuse, PDC alerts | **Cheque Settings** | `Allow Void Leaf Reuse` is off by default and should usually stay off |
| Return days, guard verification, approval workflow, visitor rules | **Gate Pass Settings** | Turning off `Enable Approval Workflow` deactivates the workflow on the next migrate |

## Development

This app uses `pre-commit` for formatting and linting. After cloning:

```bash
cd apps/erpcore
pip install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

Configured hooks:

- **ruff**: Python linting, import sorting, and formatting
- **prettier**: JS / Vue / SCSS formatting
- **eslint**: JavaScript linting
- **Frappe semgrep rules**: vendored static-analysis rules (see [`.semgrep`](.semgrep))
- **commitlint**: enforces [Conventional Commits](https://www.conventionalcommits.org/)

> The semgrep hook applies fixes automatically. Review what it changes before committing
> it is not always right about raw SQL.

Run the test suite:

```bash
bench --site your-site.com run-tests --app erpcore
```

## Continuous Integration

This repository ships a full CI/CD suite via GitHub Actions:

| Workflow | Trigger | Purpose |
| -------- | ------- | ------- |
| [`ci.yml`](.github/workflows/ci.yml) | push to `develop`, PRs | Installs ERPNext + this app on a fresh bench and runs unit tests |
| [`linter.yml`](.github/workflows/linter.yml) | PRs | Pre-commit, Frappe semgrep rules, and `pip-audit` dependency scan |
| [`semantic-commits.yml`](.github/workflows/semantic-commits.yml) | PRs | Validates commit titles against Conventional Commits |
| [`release.yml`](.github/workflows/release.yml) | push to `develop` | Automated semantic versioning + GitHub releases |

[Dependabot](.github/dependabot.yml) keeps GitHub Actions and Python dependencies up to date weekly.

## Contributing

Contributions are welcome! Please read the [Contributing Guide](CONTRIBUTING.md)
and our [Code of Conduct](CODE_OF_CONDUCT.md) before opening a pull request.

- Report bugs with the [Bug Report](https://github.com/kodlyft/erpcore/issues/new?template=bug_report.md) template.
- Suggest features with the [Feature Request](https://github.com/kodlyft/erpcore/issues/new?template=feature_request.md) template.
- Found a security issue? See the [Security Policy](SECURITY.md).

## License

[MIT](LICENSE) © [Kodlyft](https://kodlyft.com)
