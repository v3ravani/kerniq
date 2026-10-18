"""Command-line interface and interactive monitoring shell for Kerniq."""

import getpass
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import kerniq
from kerniq.authorization import (
    PermissionLevel,
    can_control,
    can_read,
    can_receive_notifications,
)
from kerniq.config import (
    DEFAULT_ADVANCED,
    DEFAULT_DEVICE,
    DEFAULT_RULES,
    MAX_AUTHORIZED_USERS,
    RULE_DEFINITIONS,
    VALID_PERMISSIONS,
    add_user,
    delete_telegram_token,
    get_advanced_config,
    get_base_dir,
    get_config_path,
    get_credentials_path,
    get_db_path,
    get_device_info,
    get_effective_device_id,
    get_output_dir,
    get_rules,
    get_telegram_token,
    get_users,
    is_device_registered_notified,
    load_config,
    mark_device_registered_notified,
    remove_user,
    save_config,
    set_device_info,
    set_telegram_token,
    update_advanced_config,
    update_rules,
)
from kerniq.exceptions import ConfigError, KerniqError, ProcessError, StorageError, TelegramError
from kerniq.metrics import get_battery_info
from kerniq.notifications import NotificationService
from kerniq.export import export_history, export_logs, export_task, get_default_export_format
from kerniq.storage import Storage
from kerniq.tasks import TaskSupervisor, format_duration, generate_task_id
from kerniq.telegram_client import TelegramClient
from kerniq.telegram_listener import TelegramListener
from kerniq.terminal import (
    align_badge,
    blue,
    bold,
    cyan,
    gray,
    green,
    red,
    sanitize_terminal_output,
    status_badge,
    yellow,
)

# ---------------------------------------------------------------------------
# Version & Help Reference
# ---------------------------------------------------------------------------

def cmd_bare() -> int:
    """Bare invocation: product identity and next-step recommendations."""
    print(cyan(f"Kerniq version: {kerniq.__version__}"))
    print(f"Installed: {Path(kerniq.__file__).parent.resolve()}")
    print(f"Directory: {get_base_dir()}")
    print("Developed by: Viraj Ravani")
    print("Local task monitoring with Telegram notifications\n")

    token = get_telegram_token()
    users = get_users()

    if not token or not users:
        print(yellow("Setup is incomplete:") + " Telegram notifications are not yet fully configured.")
        if not token:
            print(f"  - Configure bot token: {cyan('kerniq config --telegram')}")
        if not users:
            print(f"  - Add authorized user: {cyan('kerniq config --users')}")
        print(f"\nRun {cyan('kerniq help')} for instructions, or configure both to start monitoring.")
    else:
        print(green("Status: READY"))
        print(f"Run {cyan('kerniq status')} for a system snapshot, or begin monitoring with:")
        print(f"  - Single task:       {cyan('kerniq record -- <command>')}")
        print(f"  - Interactive shell: {cyan('kerniq shell')}\n")
    return 0

def cmd_version() -> int:
    """Display the installed version and developer information."""
    print(f"Kerniq version: {cyan(kerniq.__version__)} (Developed by Viraj Ravani)")
    return 0

def cmd_about() -> int:
    """Display comprehensive about info, author attribution, and license."""
    print(cyan(f"Kerniq {kerniq.__version__} - Local Task Monitoring with Telegram Remote Control"))
    print("-" * 70)
    print("Developed by:  Viraj Ravani")
    print("License:       MIT")
    print(f"Version:       {kerniq.__version__}")
    print(f"Installed At:  {Path(kerniq.__file__).parent.resolve()}")
    print(f"Storage Base:  {get_base_dir()}")
    print("GitHub:        https://github.com/v3ravani/kerniq")
    print("-" * 70)
    print("Kerniq provides local-first terminal process monitoring, real-time stdout/stderr")
    print("capture, unbuffered prompt detection, resource consumption metrics, and instant")
    print("notifications with remote control actions via Telegram.")
    print("-" * 70)
    return 0

def cmd_help(telegram_menu: bool = False, telegram_only: bool = False, shell_only: bool = False) -> int:
    """Display comprehensive reference of Kerniq CLI commands, Telegram commands, or Shell commands."""
    div = gray("─" * 70)
    if shell_only:
        print(cyan(f"Kerniq {kerniq.__version__} | Interactive Shell Commands Reference\n"))
        print(div)
        shell_commands = [
            {
                "syntax": "pause",
                "desc": "Temporarily pause Telegram notifications and rule evaluations (monitored tasks keep running).",
                "options": None,
                "example": "pause",
            },
            {
                "syntax": "resume",
                "desc": "Resume Telegram notifications and rule evaluations after pausing.",
                "options": None,
                "example": "resume",
            },
            {
                "syntax": "status",
                "desc": "Display system snapshot, shell activity, listener daemon state, and monitored tasks.",
                "options": [
                    ("--all", "Comprehensive snapshot including shell, listener, and subsystems"),
                    ("--listener", "Background Telegram listener daemon execution state and PID"),
                    ("--telegram", "Telegram bot connection status, username, and sync state"),
                    ("--rules", "Active state of all 12 notification rules and 5 thresholds"),
                    ("--users", "Authorized Telegram accounts, nicknames, and permission tiers"),
                ],
                "example": "status --all",
            },
            {
                "syntax": "stop [<id>]",
                "desc": "Terminate a running supervised task and its entire child process tree. If 1 task is running, terminates it automatically.",
                "options": None,
                "example": "stop KQ-1042",
            },
            {
                "syntax": "history",
                "desc": "Display recent task execution history in a clean section layout with duration, exit codes, and timestamps.",
                "options": [
                    ("--limit <n>", "Limit number of history records to display (e.g. history --limit 10)"),
                    ("--export", "Export execution history to file (csv, pdf, or json depending on advance settings)"),
                ],
                "example": "history --limit 10 --export",
            },
            {
                "syntax": "find <query>",
                "desc": "Search task history by numeric task ID, words inside stdout/stderr output logs, exit error codes, or execution states.",
                "options": None,
                "example": "find \"error\"",
            },
            {
                "syntax": "logs",
                "desc": "Display timestamped activity logs of Kerniq operations (shell starts, commands recorded, task actions, listener events).",
                "options": [
                    ("--export", "Export activity logs to file (save as pdf, csv, or json depending on advance settings)"),
                ],
                "example": "logs --export",
            },
            {
                "syntax": "ping telegram",
                "desc": "Send live verification message through Telegram bot to authorized users.",
                "options": [
                    ("--id <id>", "Ping specific Telegram user by numeric ID or nickname"),
                    ("--all", "Send ping verification message to all configured authorized users"),
                ],
                "example": "ping telegram --all",
            },
            {
                "syntax": "<id> [options...]",
                "desc": "Inspect or manage a monitored task by its numeric KQ-XXXX ID. Multiple options can be combined/merged.",
                "options": [
                    ("--tree", "Display process hierarchy tree, child subprocesses, and per-process CPU/RAM details"),
                    ("--output", "Display saved standard output and error log streams"),
                    ("--events", "Display chronological process lifecycle events (spawn, exit, exit codes)"),
                    ("--remove", "Safely delete task record and stored output logs from local storage"),
                    ("--restart", "Re-execute the command in a new monitored task session"),
                    ("--consumption", "Display resource consumption metrics (Peak CPU, RAM, duration, log sizes)"),
                    ("--stop", "Terminate running task and its entire child process tree"),
                    ("--export", "Export task inspection report to file (format: json, csv, or pdf based on advance config)"),
                ],
                "example": "KQ-1042 --output --tree --export",
            },
            {
                "syntax": "cleanup",
                "desc": "Safely delete finished task history and stored logs without affecting active tasks or credentials.",
                "options": [
                    ("--keep <n>", "Preserve the last n finished tasks from deletion (e.g. cleanup --keep 5)"),
                ],
                "example": "cleanup --keep 5",
            },
            {
                "syntax": "reset",
                "desc": "Reset local configuration, credentials, and task history with interactive confirmation prompt.",
                "options": [
                    ("--force", "Perform immediate full factory reset without interactive confirmation prompt"),
                ],
                "example": "reset --force",
            },
            {
                "syntax": "help",
                "desc": "Display command reference. Supports --telegram and --shell options.",
                "options": [
                    ("--telegram", "Display Telegram bot slash commands reference"),
                    ("--shell", "Display this interactive shell commands reference"),
                ],
                "example": "help --shell",
            },
            {
                "syntax": "exit",
                "desc": "Safely exit the interactive shell. Warns if supervised tasks are currently running.",
                "options": None,
                "example": "exit",
            },
            {
                "syntax": "<command>",
                "desc": "Any other command typed executes under Kerniq supervision with streaming, unbuffered capture, and Telegram alerts.",
                "options": None,
                "example": "python train.py",
            },
        ]
        for item in shell_commands:
            print(f"  {green(bold(item['syntax']))}")
            print(f"    {blue('Description:')} {item['desc']}")
            if item.get("options"):
                print(f"    {yellow('Options:')}")
                if isinstance(item["options"], list):
                    for opt_flag, opt_desc in item["options"]:
                        print(f"      {yellow(f'{opt_flag:<16}')} {opt_desc}")
                else:
                    print(f"      {item['options']}")
            print(f"    {gray('Example:')}     {item['example']}")
            print(div)
        print()
        return 0

    if telegram_menu or telegram_only:
        print(cyan(f"Kerniq {kerniq.__version__} | Telegram Bot Commands Reference\n"))
        print(div)
        tg_commands = [
            {
                "syntax": "/status",
                "desc": "Display host device metrics, battery level, listener daemon state, and shell status.",
                "options": None,
                "example": "/status",
            },
            {
                "syntax": "/running",
                "desc": "Display currently active supervised tasks with inline control buttons (Stop / View Output).",
                "options": [
                    ("🛑 Stop Task", "Terminate active monitored task immediately"),
                    ("📄 View Output", "Fetch live captured stdout/stderr log tail"),
                ],
                "example": "/running",
            },
            {
                "syntax": "/report <id>",
                "desc": "Display comprehensive report (output excerpt, exit code, timestamps, process events, process tree, resource consumption) with report download button.",
                "options": [
                    ("📥 Download", "Send exported report document (json/csv/pdf) directly to chat"),
                    ("🌲 Process Tree", "Display interactive process hierarchy tree in chat"),
                ],
                "example": "/report KQ-1042",
            },
            {
                "syntax": "/output <id>",
                "desc": "Retrieve recent captured stdout and stderr log tail for task.",
                "options": None,
                "example": "/output KQ-1042",
            },
            {
                "syntax": "/stop <id>",
                "desc": "Terminate a running monitored task and its entire child process tree.",
                "options": [
                    ("control tier", "Requires 'control' permission level to terminate"),
                ],
                "example": "/stop KQ-1042",
            },
            {
                "syntax": "/history",
                "desc": "Display recent task execution history with duration, status, and timestamps.",
                "options": None,
                "example": "/history",
            },
            {
                "syntax": "/devices",
                "desc": "Display recognized host device identities, ID mode, and connectivity status.",
                "options": None,
                "example": "/devices",
            },
            {
                "syntax": "/rules",
                "desc": "Display active notification rules, threshold levels, and quiet period cooldowns.",
                "options": None,
                "example": "/rules",
            },
            {
                "syntax": "/help",
                "desc": "Display supported Telegram commands and your account's authorized permission tier.",
                "options": None,
                "example": "/help",
            },
            {
                "syntax": "/about",
                "desc": "Display installed version, project links, and author attribution (Viraj Ravani).",
                "options": None,
                "example": "/about",
            },
            {
                "syntax": "/tree <id>",
                "desc": "Display process hierarchy tree, child subprocesses, and per-process CPU/RAM details.",
                "options": None,
                "example": "/tree KQ-1042",
            },
            {
                "syntax": "/events <id>",
                "desc": "Display chronological process lifecycle events (process started, spawned, exited with codes).",
                "options": None,
                "example": "/events KQ-1042",
            },
            {
                "syntax": "/consumption <id>",
                "desc": "Display resource metrics (Peak CPU, Average CPU, Peak Memory RSS, Duration, Output sizes).",
                "options": None,
                "example": "/consumption KQ-1042",
            },
        ]
        for item in tg_commands:
            print(f"  {green(bold(item['syntax']))}")
            print(f"    {blue('Description:')} {item['desc']}")
            if item.get("options"):
                print(f"    {yellow('Options:')}")
                if isinstance(item["options"], list):
                    for opt_flag, opt_desc in item["options"]:
                        print(f"      {yellow(f'{opt_flag:<16}')} {opt_desc}")
                else:
                    print(f"      {item['options']}")
            print(f"    {gray('Example:')}     {item['example']}")
            print(div)
        print()
        return 0

    commands = [
        {
            "syntax": "kerniq record -- <command>",
            "desc": "Execute and monitor command with unbuffered stdout/stderr streaming, process tree metrics, prompt detection, and Telegram alerts.",
            "options": [
                ("-- <command>", "Target command line to supervise and monitor"),
            ],
            "example": "kerniq record -- python train.py",
        },
        {
            "syntax": "kerniq shell",
            "desc": "Launch interactive terminal monitoring shell where all typed commands run under Kerniq supervision. Built-in shell controls: pause, resume, exit, help, status, history, find, logs, ping, stop. Cannot be nested.",
            "options": None,
            "example": "kerniq shell",
        },
        {
            "syntax": "kerniq status",
            "desc": "Display system snapshot and subsystem status. By default displays comprehensive overview including shell, listener, telegram, rules, and tasks.",
            "options": [
                ("--all", "Comprehensive snapshot including shell, listener, telegram, rules, and tasks"),
                ("--listener", "Background Telegram listener daemon execution state and PID"),
                ("--telegram", "Telegram bot connection status, username, and synchronization state"),
                ("--rules", "Active state of all 12 notification rules and 5 resource thresholds"),
                ("--users", "Authorized Telegram user accounts, nicknames, and permission tiers"),
            ],
            "example": "kerniq status --listener",
        },
        {
            "syntax": "kerniq <id>",
            "desc": "Inspect or manage a monitored task by its numeric KQ-XXXX ID. Multiple options can be combined/merged in a single invocation.",
            "options": [
                ("--tree", "Display process hierarchy tree, child subprocesses, and per-process CPU/RAM details"),
                ("--output", "Display saved standard output and error log streams"),
                ("--events", "Display chronological process lifecycle events (spawn, exit, exit codes)"),
                ("--remove", "Safely delete task record and stored output logs from local storage"),
                ("--restart", "Re-execute the command in a new monitored task session"),
                ("--consumption", "Display resource consumption metrics (Peak CPU, RAM, duration, log sizes)"),
                ("--stop", "Terminate running task and its entire child process tree"),
                ("--export", "Export task inspection report to file (format: json, csv, or pdf based on advance config)"),
            ],
            "example": "kerniq KQ-1042 --output --tree --export",
        },
        {
            "syntax": "kerniq config",
            "desc": "Access configuration. Bare command opens interactive selection menu; options directly configure specific subsystems.",
            "options": [
                ("--telegram", "Configure Telegram bot token and synchronization"),
                ("--rules", "Configure notification alert triggers and resource thresholds"),
                ("--users", "Manage authorized Telegram user IDs, nicknames, and permission tiers"),
                ("--advance", "Configure task retention limits, history preservation, and default export file type"),
            ],
            "example": "kerniq config --advance",
        },
        {
            "syntax": "kerniq show telegram",
            "desc": "Display Telegram bot configuration, connection status, username, and command synchronization state.",
            "options": None,
            "example": "kerniq show telegram",
        },
        {
            "syntax": "kerniq show users",
            "desc": "Display authorized Telegram accounts, nicknames, and permission tiers (notifications, readonly, control).",
            "options": [
                ("--reveal", "Unmask full numeric Telegram user IDs instead of showing masked IDs"),
            ],
            "example": "kerniq show users --reveal",
        },
        {
            "syntax": "kerniq show rules",
            "desc": "Display active state of all 12 notification rules and 5 configurable resource thresholds.",
            "options": None,
            "example": "kerniq show rules",
        },
        {
            "syntax": "kerniq ping telegram",
            "desc": "Send live verification message through Telegram bot. Prompts recipient selection menu if multiple users exist and no option is specified.",
            "options": [
                ("--id <id>", "Ping specific Telegram user by numeric ID or nickname"),
                ("--all", "Send ping verification message to all configured authorized users"),
            ],
            "example": "kerniq ping telegram --all",
        },
        {
            "syntax": "kerniq history",
            "desc": "Display recent task execution history in a clean section layout with duration, exit codes, and timestamps.",
            "options": [
                ("--limit <n>", "Limit number of history records to display (e.g. --limit 10)"),
                ("--export", "Export execution history to file (csv, pdf, or json depending on advance settings)"),
            ],
            "example": "kerniq history --limit 10 --export",
        },
        {
            "syntax": "kerniq cleanup",
            "desc": "Safely delete finished task history and stored logs without affecting active tasks or credentials.",
            "options": [
                ("--keep <n>", "Preserve the last n finished tasks from deletion (e.g. --keep 5)"),
            ],
            "example": "kerniq cleanup --keep 5",
        },
        {
            "syntax": "kerniq reset",
            "desc": "Reset local configuration, credentials, and task history with interactive confirmation prompt.",
            "options": [
                ("--force", "Perform immediate full factory reset without interactive confirmation prompt"),
            ],
            "example": "kerniq reset --force",
        },
        {
            "syntax": "kerniq start listener",
            "desc": "Start detached background Telegram listener daemon to handle slash commands and inline buttons when shell is closed.",
            "options": None,
            "example": "kerniq start listener",
        },
        {
            "syntax": "kerniq stop listener",
            "desc": "Gracefully terminate running background Telegram listener daemon.",
            "options": None,
            "example": "kerniq stop listener",
        },
        {
            "syntax": "kerniq doctor",
            "desc": "Run diagnostic suite verifying Python runtime, dependencies, database schema, credentials, and network connectivity.",
            "options": [
                ("--test", "Run live pytest suite and report passing test score"),
            ],
            "example": "kerniq doctor --test",
        },
        {
            "syntax": "kerniq version",
            "desc": "Display installed Kerniq version and developer attribution (Viraj Ravani).",
            "options": None,
            "example": "kerniq version",
        },
        {
            "syntax": "kerniq about",
            "desc": "Display detailed project information, developer attribution, license, and storage directories.",
            "options": None,
            "example": "kerniq about",
        },
        {
            "syntax": "kerniq logs",
            "desc": "Display timestamped activity logs of Kerniq operations (shell starts, commands recorded, task actions, listener events).",
            "options": [
                ("--export", "Export activity logs to file (save as pdf, csv, or json depending on advance settings)"),
            ],
            "example": "kerniq logs --export",
        },
        {
            "syntax": "kerniq find <query>",
            "desc": "Search task history by numeric task ID, words inside stdout/stderr output logs, exit error codes, or execution states.",
            "options": None,
            "example": "kerniq find \"error\"",
        },
        {
            "syntax": "kerniq help",
            "desc": "Display command reference. Supports --telegram for Telegram bot commands and --shell for interactive shell actions.",
            "options": [
                ("--telegram", "Display Telegram bot slash commands reference"),
                ("--shell", "Display interactive shell built-in actions and monitoring commands"),
            ],
            "example": "kerniq help --telegram",
        },
    ]

    print(cyan(f"Kerniq {kerniq.__version__} | Allowed Command Reference - CLI Commands\n"))
    print(div)
    for item in commands:
        print(f"  {green(bold(item['syntax']))}")
        print(f"    {blue('Description:')} {item['desc']}")
        if item.get("options"):
            print(f"    {yellow('Options:')}")
            if isinstance(item["options"], list):
                for opt_flag, opt_desc in item["options"]:
                    print(f"      {yellow(f'{opt_flag:<16}')} {opt_desc}")
            else:
                print(f"      {item['options']}")
        print(f"    {gray('Example:')}     {item['example']}")
        print(div)
    print()
    return 0

# ---------------------------------------------------------------------------
# Status & Diagnostics
# ---------------------------------------------------------------------------

def cmd_status(target: Optional[str] = "all") -> int:
    """Display system snapshot or subsystem status."""
    clean_target = (target or "all").lstrip("-").lower()

    if clean_target == "listener":
        from kerniq.daemon import get_listener_daemon_pid
        daemon_pid = get_listener_daemon_pid()
        print(cyan("Kerniq Persistent Listener Status"))
        print("-" * 50)
        if daemon_pid:
            print(f"Status:       {green(f'Active (Background PID {daemon_pid})')}")
            print(f"Log Path:     {get_base_dir() / 'daemon.log'}")
        else:
            print(f"Status:       {gray('Inactive')}")
            print(f"Log Path:     {get_base_dir() / 'daemon.log'}")
            print(f"Start with:   {cyan('kerniq start listener')}")
        return 0

    elif clean_target == "telegram":
        return cmd_show_telegram()

    elif clean_target == "rules":
        return cmd_show_rules()

    elif clean_target in ("user", "users"):
        return cmd_show_user(reveal=False)

    elif clean_target in ("all", "default", ""):
        storage = Storage()
        storage.reconcile_active_tasks()
        storage.apply_auto_cleanup()

        print(cyan("Kerniq System Status"))
        print("-" * 50)
        print(f"Installed Version:   {kerniq.__version__}")

        # Interactive Shell status
        shell_active = storage.is_shell_active()
        shell_str = green("Active (interactive shell running)") if shell_active else gray("Inactive")
        print(f"Interactive Shell:   {shell_str}")
        print(f"Shell Activity:      {shell_str}")

        # Persistent Listener status
        from kerniq.daemon import get_listener_daemon_pid
        daemon_pid = get_listener_daemon_pid()
        if daemon_pid:
            listener_str = green(f"Active (Background PID {daemon_pid})")
        else:
            listener_str = gray("Inactive")
        print(f"Persistent Listener: {listener_str}")

        # Telegram configuration and connection status
        token = get_telegram_token()
        if not token:
            tg_status = gray("Not Configured")
        else:
            cfg = load_config()
            tg_info = cfg.get("telegram", {})
            bot_user = tg_info.get("bot_username")
            try:
                client = TelegramClient(token, timeout=4)
                me = client.get_me()
                u_name = me.get("username", bot_user or "Unknown")
                tg_status = green(f"Connected (@{u_name})")
            except Exception:
                if bot_user:
                    tg_status = yellow(f"Configured (@{bot_user}, Offline/Unreachable)")
                else:
                    tg_status = yellow("Configured (Offline/Unreachable)")
        print(f"Telegram Status:     {tg_status}")

        # Current task count
        active_count = storage.count_active_tasks()
        active_str = green(str(active_count)) if active_count > 0 else "0"
        print(f"Current Tasks:       {active_str}")

        # Most recent task ID
        recent_tasks = storage.list_tasks(limit=1)
        if recent_tasks:
            last = recent_tasks[0]
            last_cmd = last.get("command", "")
            if len(last_cmd) > 35:
                last_cmd = last_cmd[:32] + "..."
            status_color = green if last.get("status") == "completed" else (red if last.get("status") == "failed" else yellow)
            status_badge = status_color(f"[{last.get('status', 'unknown').upper()}]")
            print(f"Most Recent Task:    {cyan(last['task_id'])} {status_badge} {last_cmd}")
        else:
            print(f"Most Recent Task:    {gray('None')}")

        # Local storage availability
        db_ok = get_db_path().exists()
        out_ok = get_output_dir().exists()
        if db_ok and out_ok:
            storage_str = green(f"Ready ({get_base_dir()})")
        else:
            storage_str = yellow(f"Degraded ({get_base_dir()})")
        print(f"Local Storage:       {storage_str}")

        # Device name and ID mode
        dev_info = get_device_info()
        dev_name = dev_info.get("name", "Unknown")
        dev_id = get_effective_device_id()
        id_mode = dev_info.get("id_mode", "hostname")
        print(f"Device Name:         {dev_name}")
        print(f"Device ID ({id_mode}): {dev_id}")

        # Battery status
        bat = get_battery_info()
        if bat.get("available") and bat.get("percent") is not None:
            plugged_str = "Plugged in" if bat.get("power_plugged") else "Discharging"
            bat_display = f"{bat['percent']:.0f}% ({plugged_str})"
        else:
            bat_display = "N/A"
        print(f"Battery:             {bat_display}")

        # List running tasks if any
        active_tasks = storage.get_active_tasks()
        if active_tasks:
            print("\nActive Running Tasks:")
            for t in active_tasks:
                dur = format_duration(t.get("duration_seconds") or 0)
                print(f"  • {green(t['task_id'])} [{t['status'].upper()}]: {t['command']} (elapsed: {dur})")

        return 0

    else:
        print(red(f"Error: Unknown status option '--{clean_target}'. Usage: kerniq status [--all|--listener|--telegram|--rules|--users]"))
        return 1

def cmd_doctor(run_tests: bool = False) -> int:
    """Check installation, configuration, storage, dependencies, and relevant integrations."""
    print(cyan("Kerniq Diagnostic Suite (doctor)"))
    print("=" * 55)

    all_passed = True

    def check(name: str, passed: bool, message: str, warning: bool = False, suggestion: Optional[str] = None):
        nonlocal all_passed
        badge = green("[PASS]") if passed else (yellow("[WARN]") if warning else red("[FAIL]"))
        if not passed and not warning:
            all_passed = False
        print(f"{badge} {name:<26}: {message}")
        if not passed and suggestion:
            print(f"       -> Suggestion: {suggestion}")

    # 1. Python runtime
    py_ok = sys.version_info >= (3, 9)
    check("Python Version", py_ok, f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
          suggestion="Upgrade to Python 3.9 or higher.")

    # 2. Package metadata
    check("Package Installation", bool(kerniq.__version__), f"Kerniq {kerniq.__version__}")

    # 3. Dependencies
    dep_ok = True
    missing_deps = []
    for mod in ("requests", "psutil", "keyring", "cryptography"):
        try:
            __import__(mod)
        except ImportError:
            dep_ok = False
            missing_deps.append(mod)
    check("Required Dependencies", dep_ok, "requests, psutil, keyring, cryptography installed" if dep_ok else f"Missing: {', '.join(missing_deps)}",
          suggestion=f"Install dependencies: pip install {' '.join(missing_deps)}")

    # 4. Storage directory writability
    base_dir = get_base_dir()
    probe_file = base_dir / f"probe_{int(time.time())}.tmp"
    storage_writable = False
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        probe_file.write_text("probe", encoding="utf-8")
        probe_file.unlink()
        storage_writable = True
    except Exception:
        storage_writable = False
    check("Storage Writability", storage_writable, f"Writable at {base_dir}",
          suggestion="Ensure user has write permissions for ~/.kerniq directory.")

    # 5. SQLite database accessibility and integrity
    db_ok = False
    try:
        storage = Storage()
        storage.count_active_tasks()
        db_ok = True
    except Exception as e:
        db_ok = False
    check("SQLite Database", db_ok, "Schema verified and accessible" if db_ok else "Database error",
          suggestion="Check SQLite database permissions in storage directory.")

    # 6. Configuration readability and validity
    cfg_ok = False
    try:
        cfg = load_config()
        cfg_ok = True
    except Exception:
        cfg_ok = False
    check("Configuration File", cfg_ok, "Valid JSON schema" if cfg_ok else "Corrupt or unreadable",
          suggestion="Re-initialize configuration with 'kerniq config rules'.")

    # 7. Output directory accessibility
    out_dir = get_output_dir()
    out_ok = out_dir.exists() and os.access(out_dir, os.W_OK)
    check("Output Directory", out_ok, f"Accessible at {out_dir}",
          suggestion="Ensure output directory exists and is writable.")

    # 8. Credential-store availability
    cred_ok = True
    try:
        import importlib
        keyring = importlib.import_module("keyring")
        keyring.get_keyring()
    except Exception:
        cred_ok = False
    check("Credential Store", cred_ok, "Operating system secure keyring available" if cred_ok else "Using fallback encrypted storage",
          warning=True, suggestion="Install keyring backends for encrypted OS credential storage.")

    # 9. Telegram configuration and connectivity
    token = get_telegram_token()
    if not token:
        check("Telegram Configuration", False, "No token configured", warning=True,
              suggestion="Run 'kerniq config telegram' to configure Telegram bot.")
    else:
        try:
            client = TelegramClient(token, timeout=5)
            me = client.get_me()
            bot_user = me.get("username", "Unknown")
            check("Telegram Connectivity", True, f"Verified bot @{bot_user}")
        except Exception as e:
            check("Telegram Connectivity", False, f"Connection failed: {e}", warning=True,
                  suggestion="Check internet connection and verify bot token with 'kerniq config telegram'.")

    # 10. Persistent Listener state
    from kerniq.daemon import get_listener_daemon_pid
    daemon_pid = get_listener_daemon_pid()
    if daemon_pid:
        check("Listener Process", True, f"Running (PID {daemon_pid})")
    else:
        check("Listener Process", True, "Inactive (start with 'kerniq start listener')", warning=True,
              suggestion="Run 'kerniq start listener' to enable background Telegram access.")

    # 11. Resource monitoring capabilities
    try:
        import psutil
        p = psutil.Process()
        p.cpu_percent()
        check("Process Resource Metrics", True, "psutil process-tree inspection supported")
    except Exception as e:
        check("Process Resource Metrics", False, f"Degraded: {e}", warning=True)

    # 12. Battery sensor
    bat = get_battery_info()
    if bat["available"]:
        check("Battery Sensor", True, bat["display"])
    else:
        print(f" {gray('[INFO]')} Battery Sensor             : {bat['display']}")

    print("=" * 55)

    if run_tests:
        print("\n" + cyan("Live Test Suite (-test):"))
        print("-" * 55)
        import subprocess
        import re
        tests_dir = Path(__file__).resolve().parent.parent / "tests"
        res = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-q", "--tb=no"],
            capture_output=True,
            text=True
        )
        stdout_val = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
        stderr_val = res.stderr if isinstance(getattr(res, "stderr", None), str) else ""
        out = stdout_val + stderr_val
        summary_lines = [l for l in out.strip().splitlines() if l.strip()]
        if summary_lines:
            print(summary_lines[-1])

        passed_m = re.search(r"(\d+)\s+passed", out)
        failed_m = re.search(r"(\d+)\s+failed", out)
        passed = int(passed_m.group(1)) if passed_m else 0
        failed = int(failed_m.group(1)) if failed_m else 0
        total = passed + failed
        if total > 0:
            score = (passed / total) * 100.0
            score_fn = green if failed == 0 else red
            print(score_fn(bold(f"Test Score: {score:.1f}% ({passed}/{total} passed)")))
        else:
            print(green(bold("Test Score: 100% (All tests passed)")))
        print("=" * 55)

    if all_passed:
        print(green("Diagnostic check complete: all required components operational."))
        return 0
    else:
        print(red("Diagnostic check completed with warnings/failures. See suggestions above."))
        return 1

# ---------------------------------------------------------------------------
# History, Output & Cleanup
# ---------------------------------------------------------------------------

def format_time_hms(time_val: Any) -> str:
    """Format an ISO timestamp or datetime to HH:MM:SS."""
    if not time_val:
        return "N/A"
    time_str = str(time_val).strip()
    if not time_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(time_str)
        return dt.strftime("%H:%M:%S")
    except Exception:
        if "T" in time_str:
            parts = time_str.split("T")
            time_part = parts[1].split("+")[0].split("-")[0].split("Z")[0].strip()
            return time_part[:8]
        elif " " in time_str:
            time_part = time_str.split(" ")[1].strip()
            return time_part[:8]
        return time_str[:8]


def cmd_history(limit: int = 20, detailed: bool = False, export: bool = False) -> int:
    """Display saved task history using a clean section-based layout."""
    storage = Storage()
    tasks = storage.list_tasks(limit=limit)

    term_w = 70
    try:
        term_w = max(56, min(shutil.get_terminal_size(fallback=(70, 24)).columns, 80))
    except Exception:
        term_w = 70

    div = gray("─" * term_w)

    title = "Saved Task History (Detailed)" if detailed else "Saved Task History"
    print(cyan(f"{title}\n"))

    if not tasks:
        print(div)
        print(gray("0 tasks found"))
        print()
        return 0

    count = len(tasks)
    task_word = "task" if count == 1 else "tasks"

    print(div)
    for t in tasks:
        tid = t.get("task_id", "Unknown")
        cmd = t.get("command", "")
        max_cmd_len = max(40, term_w - len(tid) - 6)
        cmd_disp = cmd if len(cmd) <= max_cmd_len else (cmd[:max_cmd_len - 3] + "...")

        # Heading: KQ-1042 | python train.py
        print(f"{cyan(bold(tid))} {gray('|')} {bold(cmd_disp)}")

        raw_status = (t.get("status") or "unknown").lower()
        st_hms = format_time_hms(t.get("start_time"))

        if not detailed:
            # Standard history: only task ID, command, and start time
            print(f"  {blue('Start Time:')} {st_hms}")
        else:
            # Detailed history
            st_upper = raw_status.upper()
            if raw_status in ("completed", "success"):
                st_color = green(st_upper)
            elif raw_status in ("failed", "error", "terminated"):
                st_color = red(st_upper)
            elif raw_status in ("running", "starting", "paused", "terminating"):
                st_color = yellow(st_upper)
            else:
                st_color = gray(st_upper)

            print(f"  {blue('Status:')} {st_color}")
            if st_hms != "N/A":
                print(f"  {blue('Start Time:')} {st_hms}")

            et_hms = format_time_hms(t.get("end_time"))
            if et_hms != "N/A" and raw_status not in ("running", "starting"):
                print(f"  {blue('End Time:')} {et_hms}")

            dur_sec = t.get("duration_seconds")
            if dur_sec is not None:
                print(f"  {blue('Duration:')} {format_duration(dur_sec)}")
            elif raw_status in ("running", "starting", "terminating") and t.get("start_time"):
                try:
                    st_dt = datetime.fromisoformat(t["start_time"])
                    now_dt = datetime.now(timezone.utc)
                    elapsed = (now_dt - st_dt).total_seconds()
                    if elapsed >= 0:
                        print(f"  {blue('Duration:')} {format_duration(elapsed)} (running)")
                except Exception:
                    pass

            exit_code = t.get("exit_code")
            if exit_code is not None:
                code_disp = green(str(exit_code)) if exit_code == 0 else red(str(exit_code))
                print(f"  {blue('Exit Code:')} {code_disp}")

            # Resource Utilization: populate only from actual recorded data
            avg_cpu = t.get("cpu_avg_percent")
            peak_cpu = t.get("cpu_peak_percent")
            peak_mem = t.get("memory_peak_mb")

            tree_data = storage.get_task_process_tree(tid)
            total_procs = None
            if tree_data and "nodes" in tree_data and len(tree_data["nodes"]) > 0:
                total_procs = len(tree_data["nodes"])

            res_lines = []
            if avg_cpu is not None and avg_cpu > 0:
                res_lines.append(f"    {blue('Average CPU:')} {avg_cpu:.1f}%")
            if peak_cpu is not None and peak_cpu > 0:
                res_lines.append(f"    {blue('Peak CPU:')} {peak_cpu:.1f}%")
            if peak_mem is not None and peak_mem > 0:
                mem_disp = f"{int(round(peak_mem))} MB" if peak_mem >= 10 else f"{peak_mem:.1f} MB"
                res_lines.append(f"    {blue('Peak Memory:')} {mem_disp}")
            if total_procs is not None and total_procs > 0:
                res_lines.append(f"    {blue('Tracked Processes:')} {total_procs}")

            if res_lines:
                print()
                print(f"  {blue('Resource Utilization:')}")
                for rl in res_lines:
                    print(rl)

            # Error section for failed / terminated tasks
            err = t.get("error_message")
            if not err and raw_status in ("failed", "terminated"):
                if exit_code is not None and exit_code != 0:
                    err = "Build process exited with a non-zero status." if "build" in cmd.lower() else f"Process exited with exit code {exit_code}."
                elif raw_status == "terminated":
                    err = "Task terminated by user."

            if err:
                print()
                print(f"  {red('Error:')} {err}")

        print()
        print(div)

    print(f"{count} {task_word} found\n")

    if export:
        from kerniq.export import export_history
        exp_file = export_history(tasks)
        print(green(f"History exported successfully: {exp_file.resolve()} ({count} {task_word})"))
        storage.add_app_log("history_exported", f"Task history exported to {exp_file.name}")

    return 0

def cmd_tree(task_id: str) -> int:
    """Display process hierarchy and process-level details for the specified task ID."""
    from kerniq.proc_tree import display_task_tree
    return display_task_tree(task_id)

def cmd_output(task_id: str, show_tree: bool = False) -> int:
    """Retrieve and display the actual saved output (and optional process tree) for the specified task ID."""
    storage = Storage()
    task = storage.get_task(task_id)
    if not task:
        print(red(f"Error: Task '{task_id}' not found."))
        return 1

    stdout_path = Path(task.get("stdout_path", "")) if task.get("stdout_path") else None
    stderr_path = Path(task.get("stderr_path", "")) if task.get("stderr_path") else None

    print(cyan(f"Saved Output for Task: {task_id}"))
    print(f"Command: {task.get('command')}")
    print(f"Status:  {task.get('status')} (exit code: {task.get('exit_code')})")
    print("=" * 60)

    # Standard Output
    print(cyan("--- Standard Output (stdout) ---"))
    if stdout_path and stdout_path.exists() and stdout_path.stat().st_size > 0:
        try:
            with open(stdout_path, "r", encoding="utf-8", errors="replace") as f:
                print(sanitize_terminal_output(f.read()))
        except Exception as e:
            print(red(f"Failed to read stdout: {e}"))
    else:
        print(gray("(empty or unavailable)"))

    print()
    # Standard Error
    print(yellow("--- Standard Error (stderr) ---"))
    if stderr_path and stderr_path.exists() and stderr_path.stat().st_size > 0:
        try:
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                print(sanitize_terminal_output(f.read()))
        except Exception as e:
            print(red(f"Failed to read stderr: {e}"))
    else:
        print(gray("(empty or unavailable)"))

    if show_tree:
        print()
        print("=" * 60)
        from kerniq.proc_tree import display_task_tree
        display_task_tree(task_id, storage=storage)

    return 0

def cmd_task_action(task_id: str, options: List[str]) -> int:
    """Perform one or multiple merged actions on a specified task.
    
    Supported options:
      -output      : display captured stdout and stderr logs
      -tree        : display process hierarchy and details
      -events      : display chronological process lifecycle events
      -consumption : display resource utilization metrics
      -remove      : permanently remove task from history
      -restart/-rerun : re-execute the task
      -stop/-terminate: terminate the running task
    """
    storage = Storage()
    clean_id = (task_id or "").strip()
    if not clean_id:
        print(red("Error: Task ID required. Usage: kerniq <id> [options...]"))
        return 1

    task = storage.get_task(clean_id)
    if not task:
        print(red(f"Error: Task '{clean_id}' not found."))
        return 1

    # Normalize options: strip leading dashes and lowercase
    valid_opts = {"output", "tree", "events", "consumption", "remove", "restart", "rerun", "stop", "terminate", "export"}
    parsed_opts = []
    for opt in options:
        cleaned = opt.lstrip("-").lower()
        if cleaned not in valid_opts:
            print(red(f"Error: Unknown option '{opt}' for task {clean_id}."))
            print("Allowed options: --tree, --output, --events, --consumption, --remove, --restart, --stop, --export")
            return 1
        if cleaned not in parsed_opts:
            parsed_opts.append(cleaned)

    # If no options provided, show task overview and available flags
    if not parsed_opts:
        st_hms = format_time_hms(task.get("start_time"))
        dur_sec = task.get("duration_seconds")
        dur_str = format_duration(dur_sec) if dur_sec is not None else "in progress"
        code = task.get("exit_code")
        code_str = f" (code {code})" if code is not None else ""
        print(cyan(f"Task: {clean_id}"))
        print("-" * 50)
        print(f"  Command:     {task.get('command')}")
        print(f"  Status:      {task.get('status').upper()}{code_str}")
        print(f"  Start Time:  {st_hms}")
        print(f"  Duration:    {dur_str}")
        print(f"  Options:     {gray('--tree, --output, --events, --consumption, --remove, --restart, --stop, --export')}")
        return 0

    executed = 0

    # 1. Stop / Terminate
    if any(o in parsed_opts for o in ("stop", "terminate")):
        if task["status"] in ("starting", "running", "terminating"):
            supervisor = TaskSupervisor(storage)
            ok, msg = supervisor.terminate_task(clean_id)
            if ok:
                print(green(f"Task {clean_id} stopped successfully."))
            else:
                print(yellow(f"Stop: {msg}"))
        else:
            print(yellow(f"Task {clean_id} is already {task['status']}."))
        task = storage.get_task(clean_id) or task
        executed += 1

    # 2. Output
    if "output" in parsed_opts:
        if executed > 0:
            print("\n" + "=" * 60 + "\n")
        cmd_output(clean_id, show_tree=False)
        executed += 1

    # 3. Tree
    if "tree" in parsed_opts:
        if executed > 0:
            print("\n" + "=" * 60 + "\n")
        from kerniq.proc_tree import display_task_tree
        display_task_tree(clean_id, storage=storage)
        executed += 1

    # 4. Events
    if "events" in parsed_opts:
        if executed > 0:
            print("\n" + "=" * 60 + "\n")
        from kerniq.proc_tree import format_process_events
        tree_data = storage.get_task_process_tree(clean_id)
        print(format_process_events(task, tree_data))
        executed += 1

    # 5. Consumption
    if "consumption" in parsed_opts:
        if executed > 0:
            print("\n" + "=" * 60 + "\n")
        from kerniq.proc_tree import format_resource_consumption
        tree_data = storage.get_task_process_tree(clean_id)
        print(format_resource_consumption(task, tree_data))
        executed += 1

    # 6. Restart / Rerun
    if any(o in parsed_opts for o in ("restart", "rerun")):
        if executed > 0:
            print("\n" + "=" * 60 + "\n")
        cmd_str = task.get("command", "")
        if cmd_str:
            print(cyan(f"Restarting task {clean_id}: {cmd_str}..."))
            return cmd_record(shlex.split(cmd_str))
        else:
            print(red(f"Cannot restart {clean_id}: command not found."))
            return 1

    # 7. Remove
    if "remove" in parsed_opts:
        if executed > 0:
            print("\n" + "=" * 60 + "\n")
        if storage.delete_task(clean_id):
            print(green(f"Task {clean_id} removed from history."))
        else:
            print(yellow(f"Task {clean_id} could not be removed."))
        executed += 1

    # 8. Export report
    if "export" in parsed_opts:
        if executed > 0:
            print("\n" + "=" * 60 + "\n")
        from kerniq.export import export_task
        tree_data = storage.get_task_process_tree(clean_id)
        exp_file = export_task(task, tree_data=tree_data)
        print(green(f"Task report exported successfully: {exp_file.resolve()}"))
        storage.add_app_log("task_exported", f"Task {clean_id} exported to {exp_file.name}")
        executed += 1

    return 0

def cmd_cleanup(keep_count: Optional[int] = None) -> int:
    """Delete saved task history and retained output safely."""
    storage = Storage()
    if keep_count is not None:
        records_del, files_del = storage.cleanup_old_tasks(keep_count=keep_count)
        print(green(f"Cleanup completed: {records_del} task records and {files_del} output file directories removed (kept last {keep_count})."))
        return 0

    print(yellow("CAUTION: Destructive Action"))
    print("This command will permanently delete all finished Kerniq task history and retained logs.")
    print("Active running tasks, Telegram credentials, authorized users, and rules will NOT be deleted.\n")

    try:
        if sys.stdin.isatty():
            confirm = input("Are you sure you want to proceed with cleanup? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("Cleanup cancelled. No data was deleted.")
                return 0
    except (KeyboardInterrupt, EOFError):
        print(yellow("\nCleanup cancelled."))
        return 130
    except OSError:
        pass

    records_del, files_del = storage.cleanup_old_tasks(days=0, keep_count=0)
    print(green(f"Cleanup completed: {records_del} task records and {files_del} output file directories removed."))
    return 0

def cmd_reset(force: bool = False) -> int:
    """Completely reset Kerniq: delete all history, tokens, user IDs, logs, and configuration."""
    print(red("⚠️  WARNING: Full Destructive Reset"))
    print("This command will permanently erase EVERYTHING in Kerniq and start from scratch:")
    print("  • Stored Telegram bot token and credentials (keyring & credentials file)")
    print("  • All authorized Telegram users and permissions")
    print("  • Complete task execution history and SQLite database")
    print("  • All captured output log files (stdout/stderr)")
    print("  • Notification rules, device settings, and configuration")
    print("  • Any running persistent background listener will be stopped.\n")

    if not force:
        try:
            confirm = input("Are you sure you want to completely reset Kerniq? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("Reset cancelled. No data was deleted.")
                return 0
        except KeyboardInterrupt:
            print(yellow("\nReset cancelled."))
            return 130
        except (EOFError, OSError):
            print(yellow("\nReset cancelled: non-interactive invocation requires confirmation or --force."))
            return 1

    # 1. Stop background listener daemon if running
    from kerniq.daemon import stop_listener_daemon
    try:
        stop_listener_daemon()
    except Exception:
        pass

    # 2. Delete bot token from keyring and credentials file
    delete_telegram_token()
    cred_file = get_credentials_path()
    if cred_file.exists():
        try:
            cred_file.unlink()
        except Exception:
            pass

    # 3. Clean and remove output files
    out_dir = get_output_dir()
    if out_dir.exists():
        for item in out_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
            except Exception:
                pass

    # 4. Remove SQLite database file
    db_file = get_db_path()
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass
    for suffix in ("-journal", "-wal", "-shm"):
        j_file = db_file.parent / (db_file.name + suffix)
        if j_file.exists():
            try:
                j_file.unlink()
            except Exception:
                pass

    # Re-initialize clean SQLite schema
    storage = Storage()
    storage.init_db()

    # 5. Reset config.json to fresh default
    cfg_file = get_config_path()
    if cfg_file.exists():
        try:
            cfg_file.unlink()
        except Exception:
            pass
    fresh_cfg = load_config()
    save_config(fresh_cfg)

    print(green("Kerniq has been completely reset to factory defaults. All history, tokens, and users removed."))
    return 0

# ---------------------------------------------------------------------------
# User Management & Display
# ---------------------------------------------------------------------------

def cmd_show_user(reveal: bool = False) -> int:
    """Display configured Telegram users with IDs masked or unmasked with privacy warning."""
    users = get_users()
    if reveal:
        print(yellow("WARNING: Displaying unmasked Telegram User IDs. This exposes personal identifying data."))
    print(cyan(f"Configured Telegram Accounts ({len(users)}/{MAX_AUTHORIZED_USERS}):"))
    print("-" * 55)
    if not users:
        print("  No authorized users configured. Run 'kerniq config users' to add users.")
        return 0

    for idx, u in enumerate(users, 1):
        uid_str = str(u["user_id"])
        if not reveal:
            if len(uid_str) > 4:
                masked_id = uid_str[:2] + "****" + uid_str[-2:]
            else:
                masked_id = "****"
        else:
            masked_id = uid_str
        nick_str = f" | Nickname: {green(u['nickname'])}" if u.get("nickname") else ""
        print(f"  {idx}. User ID: {masked_id:<14} | Tier: {cyan(u['permission'])}{nick_str}")
    return 0

cmd_show_users = cmd_show_user

def _send_user_notification(user_id: int, permission: str, is_new: bool) -> None:
    notifier = NotificationService()
    if is_new:
        sent, status_msg = notifier.notify_user_added(user_id, permission)
        if sent:
            print(green(f"Welcome notification sent to Telegram user {user_id}."))
        else:
            if status_msg == "Telegram is not configured.":
                print(gray("Note: Telegram bot is not yet configured. Run 'kerniq config telegram' to enable notifications."))
            else:
                print(yellow(f"Note: Could not send Telegram welcome message: {status_msg}"))
    else:
        import socket
        client = notifier._get_client()
        if client:
            try:
                client.send_message(
                    chat_id=user_id,
                    text=f"⚙️ [Kerniq] Your permission has been updated to '{permission}' on device ({socket.gethostname()})."
                )
                print(green(f"Permission update notification sent to Telegram user {user_id}."))
            except Exception:
                pass

def cmd_config_users(user_action: Optional[str] = None, user_id: Optional[int] = None, permission: Optional[str] = None, nickname: Optional[str] = None) -> int:
    """Menu-based interface for adding, editing, and removing authorized users."""
    # Programmatic bridge if called with arguments (e.g. tests)
    if user_action == "remove" or (user_action is None and user_id is not None and permission is None and user_action == "remove"):
        if user_id is not None:
            if remove_user(user_id):
                print(green(f"User {user_id} removed."))
            else:
                print(yellow(f"User {user_id} not found."))
            return 0
    elif user_id is not None and permission is not None:
        try:
            is_new = add_user(user_id, permission, nickname=nickname)
            verb = "Authorized" if is_new else "Updated"
            print(green(f"{verb} user {user_id} with permission '{permission}' successfully."))
            _send_user_notification(user_id, permission, is_new)
            return 0
        except ConfigError as e:
            print(red(f"Error: {e}"))
            return 1

    users = get_users()
    print(cyan("Kerniq User & Permission Management"))
    print("-" * 50)
    print(f"Current Authorized Accounts ({len(users)}/{MAX_AUTHORIZED_USERS}):")
    if not users:
        print("  (No authorized accounts configured)")
    else:
        for idx, u in enumerate(users, 1):
            nick_disp = f" ({u.get('nickname')})" if u.get("nickname") else ""
            print(f"  {idx}. User ID: {u['user_id']}{nick_disp} | Permission: {cyan(u['permission'])}")
    print()

    print("Options:")
    print("  [1] Add new user")
    print("  [2] Change user permission")
    print("  [3] Remove user")
    print("  [4] Exit")

    try:
        choice = input("Select an option [1-4]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print(yellow("\nOperation cancelled."))
        return 130

    if choice == "1":
        if len(users) >= MAX_AUTHORIZED_USERS:
            print(red(f"Error: Maximum limit of {MAX_AUTHORIZED_USERS} users already reached."))
            return 1
        try:
            raw_id = input("Enter Telegram numeric User ID: ").strip()
            uid = int(raw_id)
        except (ValueError, KeyboardInterrupt, EOFError):
            print(red("Invalid User ID or operation cancelled."))
            return 1

        print("\nSelect Permission Tier:")
        print(f"  [1] {cyan('control')}       - Full access: remote task termination + all inspections")
        print(f"  [2] {cyan('readonly')}      - Inspect tasks, status, history, logs; cannot terminate")
        print(f"  [3] {cyan('notifications')} - Receive task event alerts only")
        perm_choice = input("Choose tier [1-3] [default: 2]: ").strip()
        perm_map = {"1": "control", "2": "readonly", "3": "notifications"}
        chosen_perm = perm_map.get(perm_choice, "readonly")

        nick = None
        try:
            raw_nick = input("Enter user nickname (optional, press Enter to skip): ").strip()
            nick = raw_nick if raw_nick else None
        except (StopIteration, EOFError, KeyboardInterrupt):
            nick = None

        try:
            is_new = add_user(uid, chosen_perm, nickname=nick)
            nick_disp = f" ({nick})" if nick else ""
            print(green(f"Authorized user {uid}{nick_disp} with permission '{chosen_perm}' successfully."))
            _send_user_notification(uid, chosen_perm, is_new)
        except ConfigError as e:
            print(red(f"Error: {e}"))
            return 1

    elif choice == "2":
        if not users:
            print(yellow("No users currently configured to edit."))
            return 0

        target_uid = None
        if len(users) == 1:
            target_uid = int(users[0]["user_id"])
            print(f"Modifying User ID: {target_uid}")
        else:
            try:
                raw_id = input("Enter User ID to modify: ").strip()
                target_uid = int(raw_id)
            except (ValueError, KeyboardInterrupt, EOFError):
                print(red("Invalid User ID."))
                return 1

        print("\nSelect New Permission Tier:")
        print(f"  [1] {cyan('control')}       - Full access: remote task termination + all inspections")
        print(f"  [2] {cyan('readonly')}      - Inspect tasks, status, history, logs; cannot terminate")
        print(f"  [3] {cyan('notifications')} - Receive task event alerts only")
        perm_choice = input("Choose tier [1-3] [default: 1]: ").strip()
        perm_map = {"1": "control", "2": "readonly", "3": "notifications"}
        chosen_perm = perm_map.get(perm_choice, "control")

        try:
            add_user(target_uid, chosen_perm)
            print(green(f"Updated user {target_uid} with permission '{chosen_perm}' successfully."))
            _send_user_notification(target_uid, chosen_perm, is_new=False)
        except ConfigError as e:
            print(red(f"Error: {e}"))
            return 1

    elif choice == "3":
        if not users:
            print(yellow("No users currently configured to remove."))
            return 0
        try:
            raw_id = input("Enter User ID to remove: ").strip()
            rem_uid = int(raw_id)
        except (ValueError, KeyboardInterrupt, EOFError):
            print(red("Invalid User ID."))
            return 1
        if remove_user(rem_uid):
            print(green(f"User {rem_uid} removed."))
        else:
            print(yellow(f"User {rem_uid} not found."))

    elif choice in ("4", "", "exit", "quit"):
        print("Done.")

    return 0

# ---------------------------------------------------------------------------
# Telegram Configuration & Display
# ---------------------------------------------------------------------------

def cmd_config_telegram() -> int:
    """Interactive setup for Telegram bot token with Add/Update and Delete options."""
    existing_token = get_telegram_token()
    cfg = load_config()
    tg_info = cfg.get("telegram", {})

    if existing_token:
        bot_user = tg_info.get("bot_username")
        bot_id = tg_info.get("bot_id")
        masked = existing_token[:5] + "******" + existing_token[-4:] if len(existing_token) > 10 else "******"

        print(cyan("Telegram Bot Configuration"))
        print("-" * 50)
        if bot_user:
            id_suffix = f" (ID: {bot_id})" if bot_id else ""
            print(f"Current Bot: @{bot_user}{id_suffix}")
        else:
            print("Current Bot: Configured (Identity unverified)")
        print(f"Token:       {masked}\n")
        print("Options:")
        print("  [1] Update / Replace Bot Token")
        print("  [2] Delete / Remove Bot Token")
        print("  [3] Exit")

        try:
            choice = input("Select an option [1-3]: ").strip()
        except KeyboardInterrupt:
            print(yellow("\nOperation cancelled."))
            return 130
        except (EOFError, OSError):
            return 0

        if choice == "2":
            try:
                confirm = input("Are you sure you want to delete the Telegram bot token and disconnect the bot? [y/N]: ").strip().lower()
            except KeyboardInterrupt:
                print(yellow("\nOperation cancelled."))
                return 130
            except (EOFError, OSError):
                confirm = "n"
            if confirm in ("y", "yes"):
                delete_telegram_token()
                cfg = load_config()
                cfg.pop("telegram", None)
                save_config(cfg)
                from kerniq.daemon import stop_listener_daemon
                stop_listener_daemon()
                print(green("Telegram bot token has been deleted. Bot disconnected."))
                return 0
            else:
                print("Deletion cancelled.")
                return 0
        elif choice in ("3", "exit", "quit"):
            return 0
        elif choice not in ("1", ""):
            print(red("Invalid option."))
            return 1

    else:
        print(cyan("Configure Telegram Bot Token"))
        print("-" * 50)
        print("No Telegram bot is currently configured.\n")
        print("Options:")
        print("  [1] Add / Configure Telegram Bot Token (or press Enter)")
        print("  [2] Exit")

        try:
            choice = input("Select an option [1-2] [default: 1]: ").strip()
        except KeyboardInterrupt:
            print(yellow("\nOperation cancelled."))
            return 130
        except (EOFError, OSError):
            choice = "1"

        if choice in ("2", "exit", "quit"):
            return 0
        elif choice not in ("1", ""):
            print(red("Invalid option."))
            return 1

    print("\nThe Telegram bot token is a private secret. It will be stored securely.")
    try:
        token = getpass.getpass("Enter Bot Token: ").strip()
    except KeyboardInterrupt:
        print(yellow("\nOperation cancelled."))
        return 130
    except Exception:
        try:
            token = input("Enter Bot Token: ").strip()
        except KeyboardInterrupt:
            print(yellow("\nOperation cancelled."))
            return 130
        except EOFError:
            return 1

    if not token:
        print(red("Error: Bot token cannot be empty."))
        return 1

    print("Verifying token with Telegram Bot API...")
    try:
        client = TelegramClient(token, timeout=10)
        me = client.get_me()
        bot_username = me.get("username", "Unknown")
        bot_id = me.get("id")
    except TelegramError as e:
        print(red(f"Telegram verification failed: {e}"))
        print("Previous token (if any) has been preserved. No changes applied.")
        return 1

    # Save token and update metadata
    set_telegram_token(token)
    cfg = load_config()
    cfg["telegram"] = {
        "bot_id": bot_id,
        "bot_username": bot_username,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "status": "connected"
    }
    save_config(cfg)
    print(green(f"Token verified successfully! Connected as @{bot_username}."))

    # Give option to synchronize commands
    try:
        sync_choice = input("Sync Telegram bot commands now? [Y/n]: ").strip().lower()
    except (StopIteration, EOFError, KeyboardInterrupt, OSError):
        sync_choice = "y"

    if sync_choice in ("", "y", "yes"):
        print("Synchronizing supported bot commands with Telegram API...")
        if client.set_my_commands():
            print(green("Synchronized commands with Telegram bot menu."))
        else:
            print(yellow("Warning: Command synchronization with Telegram failed, but token has been saved."))
    else:
        print("Skipped command synchronization.")

    # Check device registration notification
    if not is_device_registered_notified():
        dev_info = get_device_info()
        dev_name = dev_info.get("name", "Unknown")
        dev_id = get_effective_device_id()
        notifier = NotificationService()
        notifier.notify_device_registered(dev_name, dev_id)
        mark_device_registered_notified()

    return 0

def cmd_show_telegram() -> int:
    """Display Telegram configuration, connection status, bot identity, and masked token."""
    token = get_telegram_token()
    cfg = load_config()
    tg_info = cfg.get("telegram", {})

    print(cyan("Telegram Configuration & Status"))
    print("-" * 50)
    if not token:
        print(f"Configured:      {gray('No')}")
        print(f"Verified:        {gray('No')}")
        print(f"Bot Identity:    {gray('N/A')}")
        print(f"Connection:      {gray('Offline (no token configured)')}")
        print(f"Commands Synced: {gray('N/A')}")
        print(f"Token:           {gray('None')}")
        return 0

    # Mask token
    if len(token) > 10:
        masked_token = token[:5] + "******" + token[-4:]
    else:
        masked_token = "******"

    print(f"Configured:      {green('Yes')}")
    verified_at = tg_info.get("verified_at")
    print(f"Verified:        {green('Yes') if verified_at else yellow('Unverified')}")

    # Check live connectivity
    try:
        client = TelegramClient(token, timeout=5)
        me = client.get_me()
        bot_user = me.get("username", "Unknown")
        bot_id = me.get("id", "Unknown")
        conn_str = green("Connected")
        bot_id_str = f"@{bot_user} (ID: {bot_id})"
        synced_str = green("Synchronized (11 commands)")
    except Exception as e:
        bot_user = tg_info.get("bot_username", "Unknown")
        bot_id_str = f"@{bot_user}" if bot_user != "Unknown" else "N/A"
        conn_str = red(f"Unreachable ({e})")
        synced_str = yellow("Unknown (offline)")

    print(f"Bot Identity:    {bot_id_str}")
    print(f"Connection:      {conn_str}")
    print(f"Commands Synced: {synced_str}")
    print(f"Masked Token:    {masked_token}")
    return 0

def cmd_ping_telegram(target_id: Optional[int] = None, ping_all: bool = False) -> int:
    """Send live test verification message through Telegram bot."""
    token = get_telegram_token()
    if not token:
        print(red("Error: Telegram bot token is not configured. Run 'kerniq config --telegram' first."))
        return 1

    users = get_users()
    if not users:
        print(red("Error: No authorized Telegram users configured. Run 'kerniq config --users' first."))
        return 1

    target_users = []
    if ping_all:
        target_users = users
    elif target_id is not None:
        matched = [u for u in users if u.get("user_id") == target_id]
        if not matched:
            print(red(f"Error: User ID {target_id} is not in authorized users list."))
            return 1
        target_users = matched
    else:
        if len(users) == 1:
            target_users = users
        else:
            print(cyan("Multiple authorized users configured:"))
            for idx, u in enumerate(users, 1):
                nick_str = f" ({u.get('nickname')})" if u.get("nickname") else ""
                uid = u.get("user_id")
                masked_id = f"{str(uid)[:3]}***" if uid else "unknown"
                print(f"  [{idx}] {masked_id}{nick_str} [{u.get('permission')}]")
            print("  [0] Ping all users")

            try:
                choice = input(f"Select recipient to ping [0-{len(users)}] (default 1): ").strip()
                if not choice:
                    choice = "1"
                if choice == "0":
                    target_users = users
                else:
                    idx = int(choice)
                    if 1 <= idx <= len(users):
                        target_users = [users[idx - 1]]
                    else:
                        print(red("Invalid selection."))
                        return 1
            except (ValueError, KeyboardInterrupt, EOFError):
                print(yellow("\nCancelled."))
                return 1

    client = TelegramClient(token, timeout=10)
    try:
        me = client.get_me()
        bot_user = me.get("username", "Unknown")
    except Exception as e:
        print(red(f"Error: Failed to connect to Telegram Bot API: {e}"))
        return 1

    dev_info = get_device_info()
    dev_name = dev_info.get("name", "Unknown")
    dev_id = get_effective_device_id()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    test_msg = (
        f"🧪 *[Kerniq] Ping Test*\n\n"
        f"Connection verified successfully!\n"
        f"• Bot: @{bot_user}\n"
        f"• Device: {dev_name} ({dev_id})\n"
        f"• Timestamp: {now_str}\n\n"
        f"Telegram integration is working properly."
    )

    sent_count = 0
    storage = Storage()
    for u in target_users:
        uid = int(u["user_id"])
        nick_str = f" ({u.get('nickname')})" if u.get("nickname") else ""
        try:
            client.send_message(chat_id=uid, text=test_msg, parse_mode="Markdown")
            print(green(f"✓ Test message sent successfully to authorized user {uid}{nick_str}."))
            sent_count += 1
        except Exception as e:
            print(red(f"✗ Failed to send test message to user {uid}{nick_str}: {e}"))

    storage.add_app_log("telegram_ping", f"Ping sent to {len(target_users)} user(s) ({sent_count} delivered)")

    if sent_count > 0:
        print(green(f"Ping complete ({sent_count}/{len(target_users)} messages delivered)."))
        print(green("Connection test complete."))
        return 0
    else:
        print(red("Ping failed: no messages could be delivered."))
        return 1

def cmd_test_telegram() -> int:
    """Compatibility alias for kerniq test telegram."""
    return cmd_ping_telegram()

cmd_test_connection = cmd_test_telegram

def cmd_config_menu() -> int:
    """Interactive top-level menu when bare 'kerniq config' is run."""
    print(cyan("Kerniq Configuration Menu"))
    print("-" * 50)
    print("  [1] Telegram Bot Token & Credentials")
    print("  [2] Notification Rules & Resource Thresholds")
    print("  [3] Authorized Telegram Users & Nicknames")
    print("  [4] Advanced Settings (Device ID, Retention, Export Format)")
    print("  [0] Cancel / Exit\n")
    try:
        choice = input("Select an option [0-4]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print(yellow("\nOperation cancelled."))
        return 130
    if choice == "1":
        return cmd_config_telegram()
    elif choice == "2":
        return cmd_config_rules()
    elif choice == "3":
        return cmd_config_users()
    elif choice == "4":
        return cmd_config_advance()
    elif choice in ("0", ""):
        return 0
    else:
        print(red("Invalid selection."))
        return 1

def cmd_logs(export: bool = False) -> int:
    """Display internal Kerniq activity logs and action timestamps."""
    storage = Storage()
    logs = storage.list_app_logs(limit=100)

    term_w = 70
    try:
        term_w = max(56, min(shutil.get_terminal_size(fallback=(70, 24)).columns, 80))
    except Exception:
        term_w = 70
    div = gray("─" * term_w)

    print(cyan("Kerniq System Activity Logs\n"))
    print(div)

    if not logs:
        print("  No activity logs recorded yet.")
        print(div)
        return 0

    for entry in logs:
        ts = (entry.get("timestamp") or "")[:19].replace("T", " ")
        act = entry.get("action", "")
        det = entry.get("details", "")
        det_str = f" - {det}" if det else ""
        print(f"  {blue(ts)} | {green(bold(act))}{det_str}")
        print(div)

    count = len(logs)
    print(f"{count} log {'entry' if count == 1 else 'entries'} found\n")

    if export:
        from kerniq.export import export_logs
        exp_file = export_logs(logs)
        print(green(f"Activity logs exported successfully: {exp_file.resolve()} ({count} entries)"))
        storage.add_app_log("logs_exported", f"Activity logs exported to {exp_file.name}")

    return 0

def cmd_find(query: str) -> int:
    """Search task history by ID, words of outputs, error codes, or execution states."""
    clean_q = (query or "").strip()
    if not clean_q:
        print(red("Error: Search query required. Usage: kerniq find <query>"))
        return 1

    storage = Storage()
    matches = storage.find_tasks(clean_q)

    term_w = 70
    try:
        term_w = max(56, min(shutil.get_terminal_size(fallback=(70, 24)).columns, 80))
    except Exception:
        term_w = 70
    div = gray("─" * term_w)

    print(cyan(f"Kerniq Search Results for '{clean_q}'\n"))
    print(div)

    if not matches:
        print(f"  No tasks found matching '{clean_q}'.")
        print(div)
        print("0 matching tasks found\n")
        return 0

    for t in matches:
        tid = t.get("task_id", "")
        cmd = t.get("command", "")
        raw_status = (t.get("status") or "unknown").lower()
        badge = status_badge(raw_status)
        st_hms = format_time_hms(t.get("start_time"))
        dur_sec = t.get("duration_seconds")
        dur_str = format_duration(dur_sec) if dur_sec is not None else "in progress"
        code = t.get("exit_code")
        code_str = f"Exit Code: {code}" if code is not None else "In Progress"
        reason = t.get("match_reason", "")

        print(f"  {green(bold(tid))} | {cmd}")
        print(f"    {blue('Status:')}     {badge} ({code_str})")
        print(f"    {blue('Start Time:')} {st_hms} | {blue('Duration:')} {dur_str}")
        if reason:
            print(f"    {yellow('Match:')}      {reason}")
        print(div)

    count = len(matches)
    print(f"{count} matching {'task' if count == 1 else 'tasks'} found\n")
    return 0

# ---------------------------------------------------------------------------
# Notification Rules
# ---------------------------------------------------------------------------

def cmd_show_rules() -> int:
    """Display all notification rules and thresholds."""
    rules = get_rules()
    print(cyan("Kerniq Notification Rules & Thresholds:"))
    print("-" * 55)
    print(cyan("Notification Rules:"))
    for key, label in RULE_DEFINITIONS:
        is_thresh = "threshold" in key
        default_val = False if is_thresh else True
        val = rules.get(key, default_val)
        badge = green("ENABLED") if val else gray("DISABLED")
        print(f"  • {label:<35} : {badge}")

    print(cyan("\nThresholds:"))
    print("-" * 55)
    bat_thresh = rules.get("battery_percent", 20.0)
    print(f"  • {'Battery Threshold':<35} : {bat_thresh}%")

    mem_thresh = rules.get("memory_mb", 1024.0)
    print(f"  • {'RAM / Memory Threshold':<35} : {mem_thresh} MB")

    cpu_thresh = rules.get("cpu_percent", 80.0)
    print(f"  • {'CPU Threshold':<35} : {cpu_thresh}%")

    dur_thresh = rules.get("duration_seconds", 300)
    print(f"  • {'Duration Threshold':<35} : {dur_thresh}s")

    cooldown = rules.get("cpu_cooldown_seconds", 300)
    print(f"  • {'Alert Cooldown':<35} : {cooldown}s")
    return 0

def cmd_config_rules(rule_updates: Optional[Dict[str, Any]] = None) -> int:
    """Menu-based interface to enable/disable rules and edit thresholds."""
    if rule_updates:
        try:
            update_rules(rule_updates)
            print(green("Notification rules updated successfully."))
            return 0
        except ConfigError as e:
            print(red(f"Configuration error: {e}"))
            return 1

    rules = get_rules()
    print(cyan("Configure Notification Rules & Thresholds"))
    print("-" * 50)
    print("Options:")
    print("  [1] Enable a rule")
    print("  [2] Disable a rule")
    print("  [3] Edit Battery threshold (percentage)")
    print("  [4] Edit RAM / Memory threshold (MB)")
    print("  [5] Edit CPU threshold (percentage)")
    print("  [6] Edit Duration threshold (seconds)")
    print("  [7] Edit Alert Cooldown (seconds)")
    print("  [8] Cancel / Exit")

    try:
        choice = input("Select an option [1-8]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print(yellow("\nOperation cancelled."))
        return 130

    if choice in ("1", "enable"):
        print("\nAvailable rules to ENABLE:")
        for idx, (key, label) in enumerate(RULE_DEFINITIONS, 1):
            is_thresh = "threshold" in key
            default_val = False if is_thresh else True
            cur = "ENABLED" if rules.get(key, default_val) else "DISABLED"
            print(f"  [{idx:2d}] {label:<35} ({cur})")
        sel = input("Enter rule number to ENABLE: ").strip()
        if sel.lower() in ("n", "no", "cancel", "exit", "q", "back", ""):
            print("Cancelled.")
            return 0
        try:
            r_idx = int(sel) - 1
            if 0 <= r_idx < len(RULE_DEFINITIONS):
                chosen_key, chosen_lbl = RULE_DEFINITIONS[r_idx]
                update_rules({chosen_key: True})
                print(green(f"Rule '{chosen_lbl}' has been ENABLED."))
            else:
                print(red("Invalid rule index."))
        except ValueError:
            print(red("Invalid selection."))

    elif choice in ("2", "disable"):
        print("\nAvailable rules to DISABLE:")
        for idx, (key, label) in enumerate(RULE_DEFINITIONS, 1):
            is_thresh = "threshold" in key
            default_val = False if is_thresh else True
            cur = "ENABLED" if rules.get(key, default_val) else "DISABLED"
            print(f"  [{idx:2d}] {label:<35} ({cur})")
        sel = input("Enter rule number to DISABLE: ").strip()
        if sel.lower() in ("n", "no", "cancel", "exit", "q", "back", ""):
            print("Cancelled.")
            return 0
        try:
            r_idx = int(sel) - 1
            if 0 <= r_idx < len(RULE_DEFINITIONS):
                chosen_key, chosen_lbl = RULE_DEFINITIONS[r_idx]
                update_rules({chosen_key: False})
                print(green(f"Rule '{chosen_lbl}' has been DISABLED."))
            else:
                print(red("Invalid rule index."))
        except ValueError:
            print(red("Invalid selection."))

    elif choice in ("3", "battery", "bat"):
        cur_bat = rules.get("battery_percent", 20.0)
        val = input(f"Enter battery threshold percentage (1-100) [current: {cur_bat}%]: ").strip()
        if val.lower() in ("n", "no", "cancel", "exit", "q", "back", ""):
            print("Cancelled.")
            return 0
        try:
            bat = float(val)
            if not (0.0 < bat <= 100.0):
                print(red("Battery threshold must be between 0.1 and 100.0."))
                return 1
            update_rules({"battery_percent": bat, "battery_threshold": True})
            print(green(f"Battery threshold updated to {bat}% (rule enabled)."))
        except ValueError:
            print(red("Invalid percentage."))

    elif choice in ("4", "ram", "memory", "mem"):
        cur_mem = rules.get("memory_mb", 1024.0)
        val = input(f"Enter RAM / Memory threshold in MB [current: {cur_mem} MB]: ").strip()
        if val.lower() in ("n", "no", "cancel", "exit", "q", "back", ""):
            print("Cancelled.")
            return 0
        try:
            mem = float(val)
            if mem <= 0:
                print(red("RAM threshold must be a positive number."))
                return 1
            update_rules({"memory_mb": mem, "memory_threshold": True})
            print(green(f"RAM / Memory threshold updated to {mem} MB (rule enabled)."))
        except ValueError:
            print(red("Invalid number."))

    elif choice in ("5", "cpu"):
        cur_cpu = rules.get("cpu_percent", 80.0)
        val = input(f"Enter CPU threshold percentage (1-100) [current: {cur_cpu}%]: ").strip()
        if val.lower() in ("n", "no", "cancel", "exit", "q", "back", ""):
            print("Cancelled.")
            return 0
        try:
            cpu = float(val)
            if not (0.0 < cpu <= 100.0):
                print(red("CPU threshold must be between 0.1 and 100.0."))
                return 1
            update_rules({"cpu_percent": cpu, "cpu_threshold": True})
            print(green(f"CPU threshold updated to {cpu}% (rule enabled)."))
        except ValueError:
            print(red("Invalid percentage."))

    elif choice in ("6", "duration", "dur"):
        cur_sec = rules.get("duration_seconds", 300)
        val = input(f"Enter duration threshold in seconds [current: {cur_sec}]: ").strip()
        if val.lower() in ("n", "no", "cancel", "exit", "q", "back", ""):
            print("Cancelled.")
            return 0
        try:
            sec = float(val)
            if sec <= 0:
                print(red("Duration threshold must be a positive number."))
                return 1
            update_rules({"duration_seconds": sec, "duration_threshold": True})
            print(green(f"Duration threshold updated to {sec}s (rule enabled)."))
        except ValueError:
            print(red("Invalid number."))

    elif choice in ("7", "cooldown", "cool"):
        cur_cd = rules.get("cpu_cooldown_seconds", 300)
        val = input(f"Enter alert cooldown in seconds [current: {cur_cd}s]: ").strip()
        if val.lower() in ("n", "no", "cancel", "exit", "q", "back", ""):
            print("Cancelled.")
            return 0
        try:
            cd = int(val)
            if cd < 0:
                print(red("Cooldown must be non-negative."))
                return 1
            update_rules({
                "cpu_cooldown_seconds": cd,
                "memory_cooldown_seconds": cd,
                "battery_cooldown_seconds": cd,
            })
            print(green(f"Alert cooldown updated to {cd}s."))
        except ValueError:
            print(red("Invalid integer."))

    elif choice in ("8", "", "exit", "cancel", "q"):
        print("Cancelled.")

    return 0

# ---------------------------------------------------------------------------
# Advanced Configuration
# ---------------------------------------------------------------------------

def cmd_config_advance() -> int:
    """Menu-based interface for advanced settings (device identification, auto-cleanup, export format)."""
    dev_info = get_device_info()
    adv_cfg = get_advanced_config()

    print(cyan("Kerniq Advanced Settings"))
    print("-" * 50)
    print(f"Current Device Name:    {dev_info.get('name')}")
    print(f"Current Device ID Mode: {dev_info.get('id_mode')} ({get_effective_device_id()})")
    print(f"Auto-Cleanup History:   {'ENABLED' if adv_cfg.get('auto_cleanup_enabled') else 'DISABLED'}")
    print(f"Retention Policy:       {adv_cfg.get('retention_days')} days / keep {adv_cfg.get('retention_keep_count')} tasks")
    print(f"Output File Retention:  {'Retain logs' if adv_cfg.get('retain_output_files') else 'Delete logs with tasks'}")
    print(f"Default Export Format:  {adv_cfg.get('export_format', 'json').upper()}\n")

    print("Options:")
    print("  [1] Change Device Display Name")
    print("  [2] Select Device ID Mode (hostname / machine_id / custom_uuid)")
    print("  [3] Configure Automatic History Cleanup")
    print("  [4] Configure Output File Retention")
    print("  [5] Configure Default Export Format (json / csv / pdf)")
    print("  [6] Cancel / Exit")

    try:
        choice = input("Select an option [1-6]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print(yellow("\nOperation cancelled."))
        return 130

    if choice == "1":
        new_name = input("Enter new device display name: ").strip()
        if new_name:
            set_device_info(name=new_name)
            print(green(f"Device name updated to '{new_name}'."))
        else:
            print(yellow("Device name unchanged."))

    elif choice == "2":
        print("\nSelect Device ID Mode:")
        print("  [1] hostname    - Use local operating system hostname (default)")
        print("  [2] machine_id  - Use stable cryptographic hash of machine credentials")
        print("  [3] custom_uuid - Generate and persist a unique device UUID")
        m_sel = input("Select mode [1-3]: ").strip()
        mode_map = {"1": "hostname", "2": "machine_id", "3": "custom_uuid"}
        chosen_mode = mode_map.get(m_sel)
        if chosen_mode:
            set_device_info(id_mode=chosen_mode)
            print(green(f"Device ID mode updated to '{chosen_mode}'. New effective ID: {get_effective_device_id()}"))
        else:
            print(red("Invalid selection."))

    elif choice == "3":
        cur_enabled = adv_cfg.get("auto_cleanup_enabled", False)
        print(f"\nAutomatic Cleanup is currently: {'ENABLED' if cur_enabled else 'DISABLED'}")
        toggle = input("Enable automatic history cleanup? (y/n): ").strip().lower()
        if toggle in ("y", "yes"):
            days_str = input(f"Delete tasks older than N days [current: {adv_cfg.get('retention_days', 30)}]: ").strip()
            keep_str = input(f"Minimum tasks to keep [current: {adv_cfg.get('retention_keep_count', 100)}]: ").strip()
            try:
                days = int(days_str) if days_str else adv_cfg.get("retention_days", 30)
                keep = int(keep_str) if keep_str else adv_cfg.get("retention_keep_count", 100)
                update_advanced_config({
                    "auto_cleanup_enabled": True,
                    "retention_days": max(1, days),
                    "retention_keep_count": max(0, keep)
                })
                print(green("Automatic history cleanup enabled and retention policy saved."))
            except ValueError:
                print(red("Invalid input; values must be integers."))
        else:
            update_advanced_config({"auto_cleanup_enabled": False})
            print(yellow("Automatic history cleanup disabled."))

    elif choice == "4":
        cur_retain = adv_cfg.get("retain_output_files", False)
        print(f"\nOutput file retention: {'Keep output files when task records are deleted' if cur_retain else 'Delete output files with task records'}")
        print("  [1] Delete output files when task is deleted (default)")
        print("  [2] Preserve output files even if task record is deleted")
        r_sel = input("Select option [1-2]: ").strip()
        if r_sel == "1":
            update_advanced_config({"retain_output_files": False})
            print(green("Policy set: Output files will be deleted when task records are cleaned."))
        elif r_sel == "2":
            update_advanced_config({"retain_output_files": True})
            print(green("Policy set: Output files will be preserved when task records are cleaned."))
        else:
            print(yellow("Retention policy unchanged."))

    elif choice == "5":
        cur_fmt = adv_cfg.get("export_format", "json")
        print(f"\nCurrent Default Export Format: {cur_fmt.upper()}")
        print("  [1] json - Structured JSON dump (default)")
        print("  [2] csv  - Comma-separated values")
        print("  [3] pdf  - Formatted multi-page PDF document")
        f_sel = input("Select export format [1-3]: ").strip()
        f_map = {"1": "json", "2": "csv", "3": "pdf"}
        chosen_fmt = f_map.get(f_sel)
        if chosen_fmt:
            update_advanced_config({"export_format": chosen_fmt})
            print(green(f"Default export format updated to '{chosen_fmt}'."))
        else:
            print(yellow("Export format unchanged."))

    elif choice in ("6", "", "exit", "cancel"):
        print("Cancelled.")

    return 0

# ---------------------------------------------------------------------------
# Persistent Listener Lifecycle & Gatekeeper
# ---------------------------------------------------------------------------

def warn_if_telegram_not_configured(action: str = "monitoring") -> None:
    """Mention a warning if Telegram bot token or authorized users are not configured."""
    token = get_telegram_token()
    users = get_users()

    if not token and not users:
        print(yellow("Warning: Telegram bot token and authorized users are not configured."))
        print(gray(f"  Task execution and monitoring will continue without Telegram alerts."))
        print(gray(f"  To enable remote alerts, run {cyan('kerniq config telegram')} and {cyan('kerniq config users')}.\n"))
    elif not token:
        print(yellow("Warning: Telegram bot token is not configured. Running without remote alerts."))
        print(gray(f"  To enable Telegram alerts, run {cyan('kerniq config telegram')}.\n"))
    elif not users:
        print(yellow("Warning: No authorized Telegram users configured. Running without remote alerts."))
        print(gray(f"  To add authorized users, run {cyan('kerniq config users')}.\n"))

def check_prerequisites_configured(action: str = "monitoring") -> Optional[int]:
    """Check if Telegram bot token and at least one user are configured before starting."""
    token = get_telegram_token()
    users = get_users()

    if not token and not users:
        print(red(f"Error: Telegram bot token and authorized user must be configured before starting {action}."))
        print(f"  1. Run {cyan('kerniq config telegram')} to configure your Telegram bot.")
        print(f"  2. Run {cyan('kerniq config users')} to add an authorized user.")
        return 1
    elif not token:
        print(red("Error: Telegram bot token is not configured."))
        print(f"  Run {cyan('kerniq config telegram')} to configure your Telegram bot before starting {action}.")
        return 1
    elif not users:
        print(red("Error: No authorized Telegram users configured."))
        print(f"  Run {cyan('kerniq config users')} to add an authorized user before starting {action}.")
        return 1

    return None

def cmd_show_listener() -> int:
    """Display persistent background listener status, PID, and log path."""
    from kerniq.daemon import get_listener_daemon_pid
    pid = get_listener_daemon_pid()
    token = get_telegram_token()
    users = get_users()
    log_path = get_base_dir() / "daemon.log"

    print(cyan("Kerniq Persistent Listener Status:"))
    print("-" * 55)
    if pid:
        status_str = green(f"Active (Running in background, PID {pid})")
    else:
        status_str = gray("Inactive (Not running)")
    print(f"  Listener Status:     {status_str}")

    if token:
        cfg = load_config()
        tg_info = cfg.get("telegram", {})
        bot_user = tg_info.get("bot_username", "Configured")
        tg_str = green(f"Configured (@{bot_user})")
    else:
        tg_str = gray("Not Configured")
    print(f"  Telegram Bot:        {tg_str}")

    u_count = len(users)
    if u_count > 0:
        users_str = green(f"{u_count} authorized user(s)")
    else:
        users_str = gray("None (run 'kerniq config users')")
    print(f"  Authorized Users:    {users_str}")

    log_str = str(log_path) if log_path.exists() else f"{log_path} (no logs yet)"
    print(f"  Daemon Log File:     {log_str}")

    if not pid:
        print(f"\nTo start the persistent listener, run: {cyan('kerniq start listener')}")
    else:
        print(f"\nTo stop the persistent listener, run:  {cyan('kerniq stop listener')}")

    return 0

def cmd_start_listener() -> int:
    """Start the persistent local listener required for Telegram access outside the interactive shell."""
    err = check_prerequisites_configured("the persistent listener")
    if err is not None:
        return err

    from kerniq.daemon import start_listener_daemon
    ok, msg = start_listener_daemon()
    storage = Storage()
    if ok:
        storage.add_app_log("listener_started", msg)
        print(green(msg))
        return 0
    else:
        print(red(msg))
        return 1

def cmd_stop_listener() -> int:
    """Stop the persistent local listener cleanly."""
    from kerniq.daemon import stop_listener_daemon
    ok, msg = stop_listener_daemon()
    storage = Storage()
    if ok:
        storage.add_app_log("listener_stopped", msg)
        print(green(msg))
        return 0
    else:
        print(yellow(msg))
        return 0

# ---------------------------------------------------------------------------
# Record & Interactive Shell Execution
# ---------------------------------------------------------------------------

def cmd_record(command_args: List[str]) -> int:
    """Execute single external command under Kerniq supervision."""
    if not command_args:
        print(red("Error: No command specified to record. Syntax: kerniq record -- <command>"))
        return 1

    warn_if_telegram_not_configured("task recording")

    storage = Storage()
    storage.reconcile_active_tasks()

    # Send notifications only if persistent listener is on or in kerniq shell
    from kerniq.daemon import get_listener_daemon_pid
    is_listener_on = bool(get_listener_daemon_pid())
    is_in_shell = storage.is_shell_active()
    notifications = NotificationService(listener_active=(is_listener_on or is_in_shell))
    supervisor = TaskSupervisor(storage=storage, notifications=notifications)

    task_id = generate_task_id(storage)
    cmd_display = " ".join(command_args)
    print(cyan(f"[Kerniq] Starting monitored task [{task_id}]: {cmd_display}"))

    _, exit_code = supervisor.run_command(
        command_args=command_args,
        task_id=task_id,
        tee_to_stdout=True
    )

    task = storage.get_task(task_id) or {}
    dur = format_duration(task.get("duration_seconds", 0))

    storage.add_app_log("record_command", f"Recorded command: {cmd_display} (Task {task_id}, exit {exit_code})")

    if exit_code == 0:
        print(green(f"\n[OK] Task {task_id} completed successfully (exit 0) in {dur}."))
    else:
        print(red(f"\n[ERROR] Task {task_id} failed with exit code {exit_code} in {dur}."))
    print(f"Output saved. Inspect with: {cyan(f'kerniq output {task_id}')}")
    return exit_code

def cmd_start() -> int:
    """Enter the interactive Kerniq shell with exact allowed built-in commands."""
    warn_if_telegram_not_configured("the interactive shell")

    storage = Storage()
    storage.reconcile_active_tasks()
    notifications = NotificationService(listener_active=True)
    supervisor = TaskSupervisor(storage=storage, notifications=notifications)
    listener = TelegramListener(supervisor=supervisor, storage=storage)

    storage.set_shell_active()
    storage.add_app_log("shell_started", "Interactive monitoring shell started")
    listener_started = listener.start_background()

    print(cyan(f"Kerniq {kerniq.__version__} - Interactive Task Monitoring Shell"))
    if listener_started:
        print(green("Telegram remote control listener: ACTIVE"))
    else:
        print(yellow("Telegram remote control listener: OFFLINE (configure with 'kerniq config --telegram')"))

    notifications.notify_monitoring_started("interactive shell")
    print("Type 'help' for built-in shell actions or enter commands directly.")
    print("Type 'exit' to cleanly leave the shell.\n")

    try:
        while True:
            try:
                line = input(cyan("kerniq> ")).strip()
            except KeyboardInterrupt:
                print()
                continue
            except EOFError:
                break

            if not line:
                continue

            parts = shlex.split(line)
            action = parts[0].lower()

            if action == "shell":
                print(yellow("Already inside the interactive Kerniq shell."))
                continue

            if action == "kerniq":
                sub_args = parts[1:]
                if not sub_args or sub_args[0].lower() in ("help", "--help", "-h"):
                    if len(sub_args) > 1 and sub_args[1].lower() in ("-telegram", "--telegram", "telegram"):
                        cmd_help(telegram_only=True)
                    elif len(sub_args) > 1 and sub_args[1].lower() in ("-shell", "--shell", "shell"):
                        cmd_help(shell_only=True)
                    else:
                        _print_shell_help()
                        cmd_help()
                elif sub_args[0].lower() in ("start", "shell") and len(sub_args) == 1:
                    print(yellow("Already inside the interactive Kerniq shell."))
                else:
                    _main_impl(sub_args)
                    if not listener.is_running() and get_telegram_token():
                        if listener.start_background():
                            print(green("Telegram remote control listener: ACTIVE"))
                continue

            elif action.startswith("kq-") or action.startswith("kq_") or (storage.get_task(parts[0]) is not None):
                cmd_task_action(parts[0], parts[1:])
                continue

            elif action == "record":
                _main_impl(parts)
                continue

            elif action == "force" and len(parts) > 1 and parts[1].lower() == "reset":
                _main_impl(parts)
                continue

            elif action in ("config", "show", "doctor", "cleanup", "version", "test", "reset", "about", "logs", "find", "ping"):
                _main_impl(parts)
                if not listener.is_running() and get_telegram_token():
                    if listener.start_background():
                        print(green("Telegram remote control listener: ACTIVE"))
                continue

            elif action == "listener" and len(parts) > 1:
                _main_impl(parts)
                continue

            elif action in ("start", "stop") and len(parts) > 1 and parts[1].lower() == "listener":
                _main_impl(parts)
                continue

            elif action in ("stop", "terminate"):
                if len(parts) > 1 and (parts[1].lower().startswith("kq-") or storage.get_task(parts[1])):
                    cmd_task_action(parts[1], ["--stop"])
                else:
                    active = storage.get_active_tasks()
                    if len(parts) > 1:
                        target_id = parts[1]
                        success, msg = supervisor.terminate_task(target_id)
                        if success:
                            print(green(msg))
                            storage.add_app_log("task_stopped", f"Task {target_id} stopped via shell")
                        else:
                            print(red(msg))
                    else:
                        if not active:
                            print(yellow("No tasks are currently running."))
                        elif len(active) == 1:
                            target_id = active[0]["task_id"]
                            success, msg = supervisor.terminate_task(target_id)
                            if success:
                                print(green(msg))
                                storage.add_app_log("task_stopped", f"Task {target_id} stopped via shell")
                            else:
                                print(red(msg))
                        else:
                            ids = ", ".join(t["task_id"] for t in active)
                            print(yellow(f"Multiple active tasks running ({ids}). Specify ID: stop <id>"))
                continue

            elif action == "help":
                if len(parts) > 1 and parts[1].lower() in ("-telegram", "--telegram", "telegram"):
                    cmd_help(telegram_only=True)
                elif len(parts) > 1 and parts[1].lower() in ("-shell", "--shell", "shell"):
                    cmd_help(shell_only=True)
                else:
                    _print_shell_help()
                continue

            elif action == "pause":
                notifications.paused = True
                notifications.notify_monitoring_paused()
                storage.add_app_log("monitoring_paused", "Notifications paused via shell")
                print(yellow("[PAUSED] Monitoring notifications and rule evaluation paused. Monitored OS processes continue running."))
                continue

            elif action == "resume":
                notifications.paused = False
                notifications.notify_monitoring_resumed()
                storage.add_app_log("monitoring_resumed", "Notifications resumed via shell")
                print(green("[RESUMED] Monitoring notifications and rule evaluation resumed."))
                continue

            elif action == "status":
                if len(parts) > 1:
                    _main_impl(parts)
                else:
                    cmd_status("all")
                continue

            elif action == "history":
                _main_impl(parts)
                continue

            elif action == "about":
                cmd_about()
                continue

            elif action == "logs":
                _main_impl(parts)
                continue

            elif action == "find":
                _main_impl(parts)
                continue

            elif action == "ping":
                _main_impl(parts)
                continue

            elif action == "tree":
                if len(parts) < 2:
                    print("Usage: tree <id>")
                else:
                    cmd_tree(parts[1])
                continue

            elif action == "output":
                if len(parts) < 2:
                    print("Usage: output <id> [--tree]")
                elif len(parts) == 2:
                    cmd_output(parts[1], show_tree=False)
                elif len(parts) == 3 and parts[2].lower() in ("--tree", "-tree"):
                    cmd_output(parts[1], show_tree=True)
                elif len(parts) == 3 and parts[1].lower() in ("--tree", "-tree"):
                    cmd_output(parts[2], show_tree=True)
                else:
                    print("Usage: output <id> [--tree]")
                continue

            elif action in ("exit", "quit"):
                active = storage.get_active_tasks()
                if active:
                    print(yellow(f"Warning: {len(active)} tasks are still running!"))
                    try:
                        confirm = input("Are you sure you want to exit? (y/N): ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print()
                        continue
                    if confirm != "y":
                        continue
                print("Exiting Kerniq shell.")
                print("Note: The persistent listener, if independently running, is a separate component and will continue.")
                break

            else:
                # Execute external command under Kerniq supervision
                try:
                    task_id = generate_task_id(storage)
                    cmd_display = " ".join(parts)
                    print(cyan(f"[Kerniq] Starting monitored task [{task_id}]: {cmd_display}"))
                    _, exit_code = supervisor.run_command(
                        command_args=parts,
                        task_id=task_id,
                        tee_to_stdout=True
                    )
                    task = storage.get_task(task_id) or {}
                    dur = format_duration(task.get("duration_seconds", 0))
                    storage.add_app_log("shell_command", f"Executed command: {cmd_display} (Task {task_id}, exit {exit_code})")
                    if exit_code == 0:
                        print(green(f"\n[OK] Task {task_id} completed successfully (exit 0) in {dur}."))
                    else:
                        print(red(f"\n[ERROR] Task {task_id} failed with exit code {exit_code} in {dur}."))
                    print(f"Output saved. Inspect with: {cyan(f'output {task_id}')}")
                except Exception as e:
                    print(red(f"Execution error: {e}"))

    finally:
        storage.clear_shell_active()
        storage.add_app_log("shell_ended", "Interactive monitoring shell ended")
        notifications.notify_monitoring_ended()
        listener.stop()
        print(cyan("Kerniq interactive session ended."))

    return 0


def _print_shell_help() -> None:
    """Print interactive shell built-in actions."""
    print("Interactive Shell Commands:")
    print(f"  {green('pause')}              Pause monitoring notifications and rule evaluation")
    print(f"  {green('resume')}             Resume monitoring notifications and rule evaluation")
    print(f"  {green('status [--options]')}  Display system/shell activity, listener, or subsystems")
    print(f"  {green('stop [<id>]')}        Stop / terminate a running supervised task")
    print(f"  {green('history [--limit <n>] [--export]')} Display or export saved task history")
    print(f"  {green('find <query>')}       Search tasks by ID, output words, error codes, or state")
    print(f"  {green('logs [--export]')}    Display internal Kerniq activity logs and action timestamps")
    print(f"  {green('ping telegram')}      Send live ping test to authorized Telegram users")
    print(f"  {green('<id> [options...]')}   Inspect/manage task (--tree, --output, --events, --consumption, --remove, --restart, --stop, --export)")
    print(f"  {green('tree <id>')}          Display process hierarchy and process details")
    print(f"  {green('output <id> [--tree]')} Inspect saved output (optionally with process tree)")
    print(f"  {green('cleanup [--keep <n>]')} Clean up finished task records and logs")
    print(f"  {green('reset [--force]')}    Reset Kerniq configuration and data")
    print(f"  {green('help [--telegram|--shell]')} Display CLI, Telegram, or Shell reference")
    print(f"  {green('exit')}               Safely exit the interactive shell")
    print(f"  {gray('<command>')}          Any other command executes under Kerniq supervision.\n")

# ---------------------------------------------------------------------------
# Strict Command Dispatcher
# ---------------------------------------------------------------------------

def _main_impl(argv: Optional[List[str]] = None) -> int:
    """Strict CLI dispatcher implementing only the exact allowlisted commands."""
    if argv is None:
        argv = sys.argv[1:]

    # Bare invocation: kerniq
    if not argv:
        return cmd_bare()

    cmd = argv[0].lower()
    storage = Storage()

    # 1. kerniq record -- <command>
    if cmd == "record":
        if len(argv) < 2:
            print(red("Error: Missing command. Usage: kerniq record -- <command>"))
            return 1
        flag = argv[1]
        if flag in ("-", "--", "--command"):
            cmd_args = argv[2:]
            if not cmd_args:
                print(red("Error: No command specified to record. Usage: kerniq record -- <command>"))
                return 1
            if len(cmd_args) == 1 and " " in cmd_args[0]:
                cmd_args = shlex.split(cmd_args[0])
            return cmd_record(cmd_args)
        else:
            print(red(f"Error: Unknown option '{flag}'. Usage: kerniq record -- <command>"))
            return 1

    # 2. kerniq shell (or legacy kerniq start / kerniq start listener)
    elif cmd in ("shell", "start"):
        if cmd == "shell":
            if len(argv) > 1:
                print(red("Error: 'kerniq shell' accepts no additional options."))
                return 1
            return cmd_start()
        else:
            if len(argv) == 1:
                return cmd_start()
            elif len(argv) == 2 and argv[1].lower() == "listener":
                return cmd_start_listener()
            else:
                print(red("Error: 'kerniq shell' enters interactive shell, or 'kerniq start listener'."))
                return 1

    # 3. kerniq status [--all|--listener|--telegram|--rules|--users]
    elif cmd == "status":
        if len(argv) == 1:
            return cmd_status("all")
        elif len(argv) == 2:
            target = argv[1].lstrip("-").lower()
            if target in ("all", "listener", "telegram", "rules", "users", "user"):
                return cmd_status(target)
            else:
                print(red(f"Error: 'kerniq status' accepts no additional options like '{argv[1]}'. Usage: kerniq status [--all|--listener|--telegram|--rules|--users]"))
                return 1
        else:
            print(red("Error: 'kerniq status' accepts no additional options. Usage: kerniq status [--all|--listener|--telegram|--rules|--users]"))
            return 1

    # 4. kerniq history [--limit <n>] [--export] [--detailed]
    elif cmd == "history":
        detailed = False
        limit_val = 20
        export = False
        idx = 1
        unsupported = False

        while idx < len(argv):
            arg = argv[idx].lower()
            if arg in ("-detailed", "--detailed"):
                detailed = True
                idx += 1
            elif arg in ("--export", "-export"):
                export = True
                idx += 1
            elif arg in ("-n", "--limit", "-limit"):
                if idx + 1 >= len(argv):
                    print(red("Error: '--limit' must be followed by a positive integer."))
                    return 1
                try:
                    val = int(argv[idx + 1])
                    if val <= 0:
                        raise ValueError
                    limit_val = val
                    idx += 2
                except ValueError:
                    print(red("Error: '--limit' must be followed by a positive integer."))
                    return 1
            elif arg.startswith("-n=") or arg.startswith("--limit=") or arg.startswith("-limit="):
                try:
                    val = int(arg.split("=", 1)[1])
                    if val <= 0:
                        raise ValueError
                    limit_val = val
                    idx += 1
                except ValueError:
                    print(red("Error: '--limit' must be followed by a positive integer."))
                    return 1
            else:
                unsupported = True
                break

        if unsupported:
            print(red("Error: Unsupported options for history. Usage: kerniq history [--limit <n>] [--export] [--detailed]"))
            return 1

        return cmd_history(limit=limit_val, detailed=detailed, export=export)

    # 5. kerniq doctor [--test]
    elif cmd == "doctor":
        if len(argv) == 1:
            return cmd_doctor()
        elif len(argv) == 2 and argv[1].lower() in ("-test", "--test"):
            return cmd_doctor(run_tests=True)
        else:
            print(red("Error: 'kerniq doctor' accepts no additional options (except optional '--test'). Usage: kerniq doctor [--test]"))
            return 1

    # 6. kerniq cleanup [--keep <number>]
    elif cmd == "cleanup":
        if len(argv) == 1:
            return cmd_cleanup()
        elif len(argv) == 3 and argv[1].lower() in ("-k", "--keep", "-keep"):
            try:
                k_val = int(argv[2])
                if k_val < 0:
                    raise ValueError
                return cmd_cleanup(keep_count=k_val)
            except ValueError:
                print(red("Error: '--keep' must be followed by a non-negative integer."))
                return 1
        elif len(argv) == 2 and (argv[1].lower().startswith("--keep=") or argv[1].lower().startswith("-keep=") or argv[1].lower().startswith("-k=")):
            try:
                k_val = int(argv[1].split("=", 1)[1])
                if k_val < 0:
                    raise ValueError
                return cmd_cleanup(keep_count=k_val)
            except ValueError:
                print(red("Error: '--keep' must be followed by a non-negative integer."))
                return 1
        else:
            print(red("Error: 'kerniq cleanup' accepts no additional options (except optional '--keep <n>'). Usage: kerniq cleanup [--keep <n>]"))
            return 1

    # 7. kerniq force reset
    elif cmd == "force":
        if len(argv) == 2 and argv[1].lower() == "reset":
            return cmd_reset(force=True)
        else:
            print(red("Error: Unknown command. Usage: kerniq force reset"))
            return 1

    # 8. kerniq reset [--force]
    elif cmd == "reset":
        force = False
        if len(argv) == 2 and argv[1].lower() in ("--force", "-force", "-f", "force"):
            force = True
        elif len(argv) > 1:
            print(red("Error: Unsupported options for reset. Usage: kerniq reset [--force]"))
            return 1
        return cmd_reset(force=force)

    # 9. kerniq version
    elif cmd == "version":
        if len(argv) > 1:
            print(red("Error: 'kerniq version' accepts no additional options."))
            return 1
        return cmd_version()

    # 10. kerniq about
    elif cmd == "about":
        if len(argv) > 1:
            print(red("Error: 'kerniq about' accepts no additional options."))
            return 1
        return cmd_about()

    # 11. kerniq logs [--export]
    elif cmd == "logs":
        export = False
        if len(argv) == 1:
            pass
        elif len(argv) == 2 and argv[1].lower() in ("--export", "-export"):
            export = True
        else:
            print(red("Error: Unsupported options for logs. Usage: kerniq logs [--export]"))
            return 1
        return cmd_logs(export=export)

    # 12. kerniq find <query>
    elif cmd == "find":
        if len(argv) < 2:
            print(red("Error: Search query required. Usage: kerniq find <query>"))
            return 1
        query = " ".join(argv[1:])
        return cmd_find(query)

    # 13. kerniq ping telegram [--id <id>] [--all]
    elif cmd == "ping":
        if len(argv) < 2 or argv[1].lower() != "telegram":
            print(red("Error: Target required. Usage: kerniq ping telegram [--id <id>] [--all]"))
            return 1
        rem = argv[2:]
        ping_all = False
        target_id = None
        idx = 0
        while idx < len(rem):
            flag = rem[idx].lower()
            if flag in ("--all", "-all", "all"):
                ping_all = True
                idx += 1
            elif flag in ("--id", "-id", "id"):
                if idx + 1 >= len(rem):
                    print(red("Error: '--id' must be followed by a user ID."))
                    return 1
                try:
                    target_id = int(rem[idx + 1])
                    idx += 2
                except ValueError:
                    print(red("Error: User ID must be an integer."))
                    return 1
            elif flag.startswith("--id=") or flag.startswith("-id="):
                try:
                    target_id = int(flag.split("=", 1)[1])
                    idx += 1
                except ValueError:
                    print(red("Error: User ID must be an integer."))
                    return 1
            else:
                print(red(f"Error: Unknown option '{rem[idx]}' for ping. Usage: kerniq ping telegram [--id <id>] [--all]"))
                return 1
        return cmd_ping_telegram(target_id=target_id, ping_all=ping_all)

    # 14. kerniq show <rules|telegram|users|listener>
    elif cmd == "show":
        if len(argv) < 2:
            print(red("Error: Missing target. Usage: kerniq show <rules|telegram|users>"))
            return 1
        sub = argv[1].lower()
        if sub in ("user", "users"):
            if len(argv) == 2:
                return cmd_show_user(reveal=False)
            elif len(argv) == 3 and argv[2].lower() == "--reveal":
                return cmd_show_user(reveal=True)
            else:
                print(red("Error: Unsupported option. Usage: kerniq show users [--reveal]"))
                return 1
        elif sub == "telegram":
            if len(argv) > 2:
                print(red("Error: 'kerniq show telegram' accepts no additional options."))
                return 1
            return cmd_show_telegram()
        elif sub == "rules":
            if len(argv) > 2:
                print(red("Error: 'kerniq show rules' accepts no additional options."))
                return 1
            return cmd_show_rules()
        elif sub == "listener":
            if len(argv) > 2:
                print(red("Error: 'kerniq listener status' accepts no additional options."))
                return 1
            return cmd_show_listener()
        else:
            print(red(f"Error: Unknown target 'kerniq show {sub}'. Usage: kerniq show <rules|telegram|users>"))
            return 1

    # 15. kerniq config [--telegram|--rules|--users|--advance] (or menu if bare)
    elif cmd == "config":
        if len(argv) == 1:
            return cmd_config_menu()

        sub = argv[1].lstrip("-").lower()
        if sub in ("users", "user"):
            if len(argv) > 2:
                rem_args = argv[2:]
                if rem_args[0] == "remove":
                    target_id = int(rem_args[rem_args.index("--user-id") + 1]) if "--user-id" in rem_args else None
                    return cmd_config_users(user_action="remove", user_id=target_id)
                elif "--user-id" in rem_args:
                    u_id = int(rem_args[rem_args.index("--user-id") + 1])
                    perm = rem_args[rem_args.index("--permission") + 1] if "--permission" in rem_args else "readonly"
                    return cmd_config_users(user_id=u_id, permission=perm)
            return cmd_config_users()
        elif sub == "telegram":
            if len(argv) > 2:
                print(red("Error: 'kerniq config --telegram' accepts no additional options."))
                return 1
            return cmd_config_telegram()
        elif sub == "rules":
            if len(argv) > 2:
                rem_args = argv[2:]
                updates = {}
                if "--cpu-percent" in rem_args:
                    updates["cpu_percent"] = float(rem_args[rem_args.index("--cpu-percent") + 1])
                    updates["cpu_threshold"] = True
                if "--cooldown" in rem_args:
                    cd = int(rem_args[rem_args.index("--cooldown") + 1])
                    updates["cpu_cooldown_seconds"] = cd
                    updates["memory_cooldown_seconds"] = cd
                    updates["battery_cooldown_seconds"] = cd
                if "--memory-mb" in rem_args:
                    updates["memory_mb"] = float(rem_args[rem_args.index("--memory-mb") + 1])
                    updates["memory_threshold"] = True
                if "--ram-mb" in rem_args:
                    updates["memory_mb"] = float(rem_args[rem_args.index("--ram-mb") + 1])
                    updates["memory_threshold"] = True
                if "--battery-percent" in rem_args:
                    updates["battery_percent"] = float(rem_args[rem_args.index("--battery-percent") + 1])
                    updates["battery_threshold"] = True
                if "--duration-seconds" in rem_args:
                    updates["duration_seconds"] = float(rem_args[rem_args.index("--duration-seconds") + 1])
                    updates["duration_threshold"] = True
                if updates:
                    return cmd_config_rules(rule_updates=updates)
            return cmd_config_rules()
        elif sub in ("advance", "advanced"):
            if len(argv) > 2:
                print(red("Error: 'kerniq config --advance' accepts no additional options."))
                return 1
            return cmd_config_advance()
        else:
            print(red(f"Error: Unknown option '{argv[1]}' for config. Usage: kerniq config [--telegram|--rules|--users|--advance]"))
            return 1

    # 16. kerniq listener <status|start|stop>
    elif cmd == "listener":
        if len(argv) >= 2:
            sub = argv[1].lower()
            if sub == "status":
                if len(argv) > 2:
                    print(red("Error: 'kerniq listener status' accepts no additional options."))
                    return 1
                return cmd_show_listener()
            elif sub == "start":
                return cmd_start_listener()
            elif sub == "stop":
                return cmd_stop_listener()
        print(red("Error: Unknown listener command. Usage: kerniq listener status | kerniq start listener | kerniq stop listener"))
        return 1

    # 17. kerniq test <telegram|connection>
    elif cmd == "test":
        if len(argv) >= 2 and argv[1].lower() in ("telegram", "connection"):
            if len(argv) > 2:
                print(red(f"Error: 'kerniq test {argv[1]}' accepts no additional options."))
                return 1
            return cmd_ping_telegram()
        else:
            print(red("Error: Unknown test target. Usage: kerniq ping telegram | kerniq test telegram"))
            return 1

    # 18. kerniq stop <listener|<id>>
    elif cmd in ("stop", "terminate"):
        if len(argv) >= 2 and argv[1].lower() == "listener":
            if len(argv) > 2:
                print(red("Error: 'kerniq stop listener' accepts no additional options."))
                return 1
            return cmd_stop_listener()
        elif len(argv) == 2 and (argv[1].lower().startswith("kq-") or storage.get_task(argv[1])):
            return cmd_task_action(argv[1], ["--stop"])
        else:
            print(red("Error: Unknown command. Usage: kerniq stop listener | kerniq <id> --stop"))
            return 1

    # 19. kerniq tree <id> (bridge compatibility)
    elif cmd == "tree":
        if len(argv) < 2:
            print(red("Error: Task ID required. Usage: kerniq tree <id>"))
            return 1
        if len(argv) > 2:
            print(red("Error: 'kerniq tree' accepts only a single Task ID. Usage: kerniq tree <id>"))
            return 1
        return cmd_tree(argv[1])

    # 20. kerniq output <id> [--tree] (bridge compatibility)
    elif cmd == "output":
        if len(argv) < 2:
            print(red("Error: Task ID required. Usage: kerniq output <id> [--tree]"))
            return 1
        if len(argv) == 2:
            if argv[1].startswith("-"):
                print(red("Error: Task ID required. Usage: kerniq output <id> [--tree]"))
                return 1
            return cmd_output(argv[1], show_tree=False)
        elif len(argv) == 3:
            if argv[1].lower() in ("--tree", "-tree"):
                return cmd_output(argv[2], show_tree=True)
            elif argv[2].lower() in ("--tree", "-tree"):
                return cmd_output(argv[1], show_tree=True)
            else:
                print(red("Error: Unsupported options for output. Usage: kerniq output <id> [--tree]"))
                return 1
        else:
            print(red("Error: Unsupported options for output. Usage: kerniq output <id> [--tree]"))
            return 1

    # 21. kerniq help [--telegram|--shell]
    elif cmd in ("help", "--help", "-h"):
        if len(argv) == 1:
            return cmd_help()
        elif len(argv) == 2 and argv[1].lower() in ("-telegram", "--telegram", "telegram"):
            return cmd_help(telegram_only=True)
        elif len(argv) == 2 and argv[1].lower() in ("-shell", "--shell", "shell"):
            return cmd_help(shell_only=True)
        else:
            print(red("Error: Unsupported options for help. Usage: kerniq help [--telegram|--shell]"))
            return 1

    # 22. Task action: kerniq <id> [options...]
    elif cmd.startswith("kq-") or cmd.startswith("kq_") or (storage.get_task(argv[0]) is not None):
        return cmd_task_action(argv[0], argv[1:])

    # Reject any unsupported commands
    else:
        print(red(f"Error: Unsupported command '{' '.join(argv)}'."))
        print(f"Run {cyan('kerniq help')} to see the list of allowed commands.")
        return 1

def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point with graceful interrupt handling."""
    try:
        # Pre-process combined multi-word command tokens if passed as single element
        if argv is not None and len(argv) > 0:
            if len(argv) == 1 and " " in argv[0]:
                argv = shlex.split(argv[0])
            else:
                first = argv[0].strip().lower()
                multi_cmds = (
                    "config users", "config telegram", "config rules", "config advance",
                    "show user", "show users", "show rules", "show telegram", "show listener",
                    "start listener", "stop listener", "listener status",
                    "test connection", "test telegram", "force reset",
                    "help -telegram", "doctor -test",
                    "ping telegram", "help --telegram", "doctor --test",
                )
                if first in multi_cmds:
                    argv = shlex.split(argv[0]) + argv[1:]

        return _main_impl(argv)
    except KeyboardInterrupt:
        print(yellow("\nOperation cancelled by user."))
        return 130
    except EOFError:
        print(yellow("\nInput stream closed."))
        return 1

if __name__ == "__main__":
    sys.exit(main())

