<div align="center">

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

**ERP Core** is a Frappe app that packages shared, cross-cutting requirements used
across Kodlyft's ERPNext deployments, so they live in one versioned place instead of
being duplicated per site.

> **Status: early development.** The app scaffolding, CI, and release automation are in
> place, but no functionality has shipped yet. Expect the surface area to change without
> notice until a `1.0` release. Watch the [releases](https://github.com/kodlyft/erpcore/releases)
> page for progress.

## Requirements

| Dependency       | Version         |
| ---------------- | --------------- |
| Frappe Framework | v16             |
| ERPNext          | v16 (required)  |
| Python           | >= 3.14         |

ERPNext is a hard dependency — it is declared in `required_apps`, so installing
`erpcore` on a site without ERPNext will fail.

## Installation

Install with the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/kodlyft/erpcore --branch develop
bench --site your-site.com install-app erpcore
```

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
