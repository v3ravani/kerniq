# Security Policy

The **Kerniq** project takes the security, confidentiality, and integrity of your developer environment and credentials seriously. As a local-first utility that manages command execution, process tracking, and Telegram bot communication, protecting sensitive credentials and local task data is a fundamental design requirement.

---

## Supported Versions

Security updates and patches are actively provided for the following releases:

| Version | Supported | Notes |
|---|:---:|---|
| `1.0.x` | Yes | Current stable release branch. Receives bug and security fixes. |
| `< 1.0.0` | No | Development / pre-release versions. Please upgrade to `1.0.0` or newer. |

---

## Reporting a Vulnerability

If you discover a security vulnerability or potential exploit in Kerniq, **please do not disclose it publicly** through GitHub Issues, Discussions, or social media until it has been reviewed and addressed.

### Reporting Channels

Please report vulnerabilities privately via either of the following channels:

1. **Email (Direct Maintainer Contact):**
   Send full details to **Viraj Ravani** at:  
    `viraj.ravani@somaiya.edu`

2. **GitHub Private Vulnerability Reporting:**
   If available on GitHub, submit an advisory through:  
   [GitHub Security Advisories](https://github.com/v3ravani/kerniq/security/advisories/new)

### What to Include in Your Report

To help us investigate and resolve the issue quickly, please include:
- A clear description of the vulnerability and its potential security impact.
- Affected Kerniq versions and host operating systems (Windows, Linux, macOS).
- Step-by-step instructions to reproduce the issue, including minimal reproducible commands or proof-of-concept (PoC) code.
- Any suggested mitigations or patches, if known.

### Coordinated Disclosure Process

1. **Acknowledgment:** We will acknowledge receipt of your report within 48 hours.
2. **Investigation & Assessment:** We will verify the vulnerability, evaluate its severity across supported platforms, and keep you informed of our progress.
3. **Fix & Verification:** We will prepare a patch, run the full automated test suite, and verify the remediation.
4. **Release & Announcement:** A security release will be published to PyPI and GitHub with credit to the reporter (unless you request anonymity).

We kindly ask that you observe responsible disclosure and maintain confidentiality until the security release is publicly available.

---

## Security Architecture & Best Practices

Kerniq is engineered with privacy and security as first principles:

### 1. Local-First Data Storage
- All stdout/stderr logs (`~/.kerniq/outputs/`), execution histories, and metadata stored in SQLite (`~/.kerniq/kerniq.db`) remain strictly on your local computer.
- Kerniq contains no telemetry, cloud data syncing, or third-party analytical trackers.

### 2. Machine-Bound Encryption
- Telegram bot tokens and authorized Telegram user IDs are **never written to disk in plaintext**.
- Sensitive secrets are encrypted with Fernet symmetric cryptography using a machine-bound encryption key stored with restricted permissions (`~/.kerniq/.key`).
- Plaintext credentials are kept strictly in-memory during active sessions.

### 3. Role-Based Authorization
- Kerniq verifies the sender's immutable numeric Telegram user ID against authorized records on every slash command and button callback.
- Access is strictly governed across three permission tiers (`notifications`, `readonly`, and `control`). Unrecognized user IDs are rejected and logged.

### 4. Process Tree Isolation
- Process termination routines target only the specific, tracked process tree recorded in the local SQLite database. Kerniq never performs fuzzy name matching to kill unrelated operating system processes.

### 5. Terminal Injection Mitigation
- Output logs viewed in terminal sessions or forwarded via Telegram pass through ANSI control sequence sanitization to protect against terminal escape injection attacks.
