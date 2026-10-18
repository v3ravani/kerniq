# Contributing to Kerniq

Thank you for your interest in contributing to **Kerniq**! We welcome bug reports, documentation enhancements, feature proposals, and code contributions from developers of all backgrounds.

Kerniq is built as a **local-first, privacy-respecting terminal task monitoring utility** with Telegram notifications and remote control. All contributions should align with these core principles.

---

## Table of Contents

- [Guiding Principles](#guiding-principles)
- [How to Contribute](#how-to-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Features](#suggesting-features)
  - [Submitting Pull Requests](#submitting-pull-requests)
- [Development Setup](#development-setup)
- [Running the Test Suite](#running-the-test-suite)
- [Code Style and Quality Standards](#code-style-and-quality-standards)
- [Commit Message Conventions](#commit-message-conventions)

---

## Guiding Principles

When proposing changes or submitting pull requests, keep these foundational rules in mind:

1. **Local-First & Zero Telemetry:** Source code, command arguments, task logs, and SQLite records must remain strictly on the user's workstation. Do not introduce cloud dependencies, remote analytics, or telemetry.
2. **Security & Privacy:** Telegram tokens and authorized user IDs must always be encrypted machine-bound using Fernet cryptography (`~/.kerniq/.key`). Never log or expose credentials in plaintext.
3. **Cross-Platform Compatibility:** Kerniq supports Windows, Linux, and macOS. Process supervision, stream piping, process-tree termination, and terminal styling must work reliably across supported operating systems.
4. **Predictable CLI & Shell Interfaces:** Command-line syntax must remain consistent, predictable, and compliant with standard conventions (such as `--help`, `--version`, and the `NO_COLOR` standard).
5. **High Test Coverage:** Every bug fix, feature addition, or CLI command modification must be accompanied by unit or integration tests in `tests/`.

---

## How to Contribute

### Reporting Bugs

Before creating a new issue, search existing [GitHub Issues](https://github.com/v3ravani/kerniq/issues) to ensure the bug has not already been reported.

If you encounter a bug, open an issue with the following details:
- **Environment:** Operating system and version, Python version (`python --version`), and Kerniq version (`kerniq version`).
- **Health Check Output:** Run `kerniq doctor` and include the terminal output.
- **Steps to Reproduce:** Exact command(s) executed and terminal input.
- **Observed vs. Expected Behavior:** What happened versus what you expected to happen.
- **Log Snippets:** Include relevant output from `~/.kerniq/daemon.log` or captured task logs in `~/.kerniq/outputs/` (sanitize any private tokens or sensitive command parameters first).

> [!NOTE]
> If you have discovered a **security vulnerability**, please review our [Security Policy](SECURITY.md) and report it privately rather than opening a public issue.

### Suggesting Features

We welcome ideas that enhance developer workflow without compromising simplicity:
- Open a feature request on [GitHub Issues](https://github.com/v3ravani/kerniq/issues).
- Describe the problem you are trying to solve and your proposed CLI or Telegram command design.
- Outline any alternative approaches or trade-offs you considered.

### Submitting Pull Requests

1. **Fork the Repository:** Create your personal fork of [v3ravani/kerniq](https://github.com/v3ravani/kerniq).
2. **Create a Feature Branch:** Branch off `main` with a descriptive name:
   ```bash
   git checkout -b feature/process-tree-export
   ```
3. **Develop & Test:** Write your changes, adhere to code quality standards, and ensure all existing and new tests pass.
4. **Update Documentation:** If you add or modify commands, options, rules, or Telegram handlers, update `README.md` and `CHANGELOG.md`.
5. **Commit & Push:** Commit your changes with clear, descriptive messages and push to your fork:
   ```bash
   git push origin feature/process-tree-export
   ```
6. **Open a Pull Request:** Open a PR against the `main` branch of `v3ravani/kerniq`. Provide a concise summary of what was changed and reference any related issues.

---

## Development Setup

### Prerequisites

- **Python:** Python 3.9, 3.10, 3.11, 3.12, 3.13, or 3.14.
- **Git:** Git 2.20 or newer.

### Local Installation

1. Clone your fork or the repository:
   ```bash
   git clone https://github.com/v3ravani/kerniq.git
   cd kerniq
   ```

2. Create and activate a dedicated virtual environment:
   ```bash
   # On Windows:
   python -m venv venv
   venv\Scripts\activate

   # On Linux / macOS:
   python -m venv venv
   source venv/bin/activate
   ```

3. Install Kerniq in editable mode with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

4. Verify your local installation:
   ```bash
   kerniq version
   kerniq doctor
   ```

---

## Running the Test Suite

Kerniq includes an automated test suite covering authorization, CLI dispatch, configuration encryption, metrics, process supervision, storage, and Telegram communication.

Run the test suite with `pytest`:

```bash
pytest
```

Run with test coverage reporting:

```bash
pytest --cov=kerniq tests/
```

Run tests directly through Kerniq's diagnostic doctor:

```bash
kerniq doctor --test
```

All pull requests must pass 100% of the automated test suite prior to review and merging.

---

## Code Style and Quality Standards

- **PEP 8 Compliance:** Follow standard Python PEP 8 conventions for code style, variable naming, and layout.
- **Type Annotations:** Use type annotations for function signatures and public APIs.
- **Error Handling:** Use custom exceptions from `kerniq.exceptions` rather than raising raw generic exceptions.
- **Subprocess Safety:** Handle process termination safely and recursively through `psutil`, ensuring child processes are cleanly cleaned up on both Windows and POSIX systems.
- **Secret Hygiene:** Never write unencrypted tokens or credentials to logs or database tables.
- **Documentation Integrity:** Preserve docstrings and comments. Update [README.md](README.md) and [CHANGELOG.md](CHANGELOG.md) to reflect user-facing modifications.

---

## Commit Message Conventions

Write clear, imperative commit messages that explain *what* changed and *why*:

```text
Add --export option to history command

Support exporting recent execution history as JSON, CSV, or PDF
based on advanced configuration settings.
```

- Keep the first line under 72 characters.
- Use the imperative mood (e.g., `Add`, `Fix`, `Refactor`, `Update`).
- Reference issue numbers where applicable (e.g., `Fixes #12`).
