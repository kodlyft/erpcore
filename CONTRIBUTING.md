# Contributing to ERP Core

Thank you for your interest in contributing to ERP Core! This guide will help you get started.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Commit Guidelines](#commit-guidelines)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Requesting Features](#requesting-features)

## Code of Conduct

This project follows a [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork into your bench:

   ```bash
   cd $PATH_TO_YOUR_BENCH
   bench get-app https://github.com/<your-username>/erpcore --branch develop
   bench --site your-site.com install-app erpcore
   ```

3. **Add the upstream remote:**

   ```bash
   cd apps/erpcore
   git remote add upstream https://github.com/kodlyft/erpcore.git
   ```

4. **Create a new branch** from `develop`:

   ```bash
   git checkout -b feat/your-feature-name develop
   ```

## Development Setup

### Prerequisites

| Dependency       | Version  |
| ---------------- | -------- |
| Python           | >= 3.14  |
| Node.js          | >= 24    |
| Frappe Framework | v16      |
| ERPNext          | v16 (required) |

### Pre-commit Hooks

Install pre-commit hooks to ensure code quality before every commit:

```bash
cd apps/erpcore
pip install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

### Backend Development

```bash
cd $PATH_TO_YOUR_BENCH
bench start       # Start Frappe development server
```

## Making Changes

- Keep changes focused. A single PR should address a single concern.
- Write or update tests for any new functionality.
- Ensure all linters and tests pass before submitting:

  ```bash
  # Python linting
  ruff check erpcore/

  # Run the app's unit tests
  bench --site your-site.com run-tests --app erpcore
  ```

## Commit Guidelines

All commits **must** follow the [Conventional Commits](https://www.conventionalcommits.org/) specification. This enables automated versioning and changelog generation.

### Format

```
<type>(<optional scope>): <description>

[optional body]

[optional footer(s)]
```

### Allowed Types

| Type       | Description                              |
| ---------- | ---------------------------------------- |
| `feat`     | A new feature                            |
| `fix`      | A bug fix                                |
| `docs`     | Documentation only changes               |
| `style`    | Formatting, missing semicolons, etc.     |
| `refactor` | Code change that neither fixes nor adds  |
| `perf`     | Performance improvement                  |
| `test`     | Adding or updating tests                 |
| `build`    | Build system or dependency changes       |
| `ci`       | CI configuration changes                 |
| `chore`    | Other changes that don't modify src/test |
| `revert`   | Reverts a previous commit                |

### Examples

```
feat: add ZATCA phase 2 e-invoice QR generation
fix: correct VAT rounding on credit notes
docs: document tax category setup
refactor(vat): simplify tax template resolution
test: add unit tests for VAT return report
```

## Pull Request Process

1. **Sync your branch** with the latest `develop`:

   ```bash
   git fetch upstream
   git rebase upstream/develop
   ```

2. **Push** your branch and open a Pull Request against the `develop` branch.

3. Fill out the PR template completely describe what changed and why.

4. Ensure all CI checks pass (linting, semgrep, tests).

5. A maintainer will review your PR. Address any feedback and push updates to the same branch.

6. Once approved, a maintainer will merge your PR.

## Reporting Bugs

Use the [Bug Report](https://github.com/kodlyft/erpcore/issues/new?template=bug_report.md) issue template. Please include:

- Steps to reproduce
- Expected vs. actual behavior
- Error logs / traceback if applicable
- Your environment (Frappe version, Python version, OS)

## Requesting Features

Use the [Feature Request](https://github.com/kodlyft/erpcore/issues/new?template=feature_request.md) issue template. Describe:

- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

---

Thank you for contributing to ERP Core!
