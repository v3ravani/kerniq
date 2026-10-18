# Changelog

All notable changes to Kerniq will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-16

### Added
- Local-first CLI utility for monitoring terminal tasks.
- Monitored single-command execution via `kerniq record -- <command>`.
- Interactive monitoring shell via `kerniq start` with session management and built-in actions (`help`, `status`, `pause`, `resume`, `history`, `output`, `exit`).
- SQLite-backed local persistence for task metadata, execution history, and state transitions.
- Streaming output capture for stdout and stderr without process deadlocks.
- Safe terminal output inspection via `kerniq output <ID>` with ANSI sanitization and tail options.
- Telegram Bot API integration for real-time notifications and authorized remote queries.
- Strict authorization model with numeric Telegram user IDs and up to 3 authorized accounts across three permission tiers (`notifications`, `readonly`, `control`).
- Telegram slash commands: `/help`, `/status`, `/running`, `/history`, `/rules`, `/output <ID>`, and `/terminate <ID>`.
- Interactive Telegram buttons for task termination and output viewing with server-side re-validation.
- CPU and RSS memory resource monitoring with configurable thresholds, consecutive sample requirements, and anti-spam cooldowns.
- `kerniq doctor` diagnostic suite with automated health checks for local storage, permissions, secret stores, and process management.
- Complete terminal experience supporting semantic colors, TTY detection, and `NO_COLOR` standard compliance.
- Safe process tree tracking and termination on Windows and POSIX systems.
- Process tree inspection command `kerniq tree <id>` displaying full process hierarchies, states, CPU/memory consumption, durations, and exit codes.
- Extended `kerniq output <id>` with optional `--tree` flag to display captured stdout/stderr alongside the process hierarchy.
- Refined `kerniq history` terminal layout to use clean, section-based command entries with horizontal divider lines instead of a table.
- Added `kerniq history --detailed` supporting comprehensive execution metadata, exit codes, process counts, resource utilization (average CPU, peak CPU, peak memory), and error messages.
- Permitted starting the interactive shell (`kerniq shell`) and recording single commands (`kerniq record - <cmd>`) without Telegram or authorized users configured, displaying an informative warning while executing locally.
- Replaced `kerniq start` with `kerniq shell`, enabling full execution of all standard CLI commands, task actions, and built-in session controls inside the interactive shell.
- Added unified task action command `kerniq <id>` supporting merged options: `-output`, `-tree`, `-events`, `-consumption`, `-remove`, `-restart`/`-rerun`, and `-stop`/`-terminate`.
- Enforced strictly numeric 4-digit task IDs (`KQ-XXXX`, 1000-9999).
- Implemented Fernet machine-bound encryption for Telegram bot tokens (`credentials.json`) and authorized Telegram user IDs (`config.json`), eliminating all plaintext credential persistence.
- Added Telegram user nickname support during `kerniq config users` and display in `kerniq show users`.
- Added automated command synchronization prompt when configuring Telegram bot token.
- Added live test verification and scoring to diagnostics via `kerniq doctor -test`.
- Added retention keep-count flag `kerniq cleanup -k <number>` to retain the most recent `<n>` task records.
- Added immediate non-interactive factory reset via `kerniq force reset`.
- Added dedicated Telegram bot command help reference via `kerniq help -telegram`.
- Added `/about`, `/tree <id>`, `/events <id>`, and `/consumption <id>` Telegram bot commands and corresponding inline button callback handlers.
- Documented developer attribution to Viraj Ravani across CLI, Telegram `/about`, and documentation.

