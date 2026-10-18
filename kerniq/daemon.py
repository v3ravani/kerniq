"""Background daemon process management for the Kerniq Telegram listener."""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple
import psutil

from kerniq.config import get_base_dir, get_telegram_token
from kerniq.telegram_client import TelegramClient

PID_FILE_NAME = "listener.pid"

def get_pid_file() -> Path:
    """Return path to the listener PID file."""
    return get_base_dir() / PID_FILE_NAME

def get_listener_daemon_pid() -> Optional[int]:
    """Return PID of running listener daemon, or None if not running.
    
    Automatically cleans up stale PID files if the process is dead.
    """
    pid_file = get_pid_file()
    if not pid_file.exists():
        return None

    try:
        pid_text = pid_file.read_text(encoding="utf-8").strip()
        pid = int(pid_text)
    except Exception:
        pid_file.unlink(missing_ok=True)
        return None

    try:
        proc = psutil.Process(pid)
        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
            try:
                cmdline = " ".join(proc.cmdline())
                if "kerniq" in cmdline and "listener" in cmdline:
                    return pid
            except (psutil.AccessDenied, Exception):
                # If command-line inspection is restricted but process is alive python
                if "python" in (proc.name() or "").lower():
                    return pid
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        pass
    except psutil.AccessDenied:
        return pid

    # Process is no longer running; clean up stale file
    pid_file.unlink(missing_ok=True)
    return None

def start_listener_daemon() -> Tuple[bool, str]:
    """Start the Telegram listener as a detached background daemon process."""
    token = get_telegram_token()
    from kerniq.config import get_users
    users = get_users()

    if not token and not users:
        return False, "Telegram bot token and authorized user must be configured. Run 'kerniq config telegram' and 'kerniq config users' first."
    if not token:
        return False, "Telegram bot token is not configured. Run 'kerniq config telegram' first."
    if not users:
        return False, "No authorized Telegram users configured. Run 'kerniq config users' first."

    # Always ensure official bot slash commands are synced to Telegram UI menu
    try:
        client = TelegramClient(token)
        client.set_my_commands()
    except Exception:
        pass

    existing_pid = get_listener_daemon_pid()
    if existing_pid:
        return True, f"Telegram listener is already running in background (PID {existing_pid})."

    # Launch detached persistent process running run_daemon_listener()
    python_exe = sys.executable
    cmd = [python_exe, "-u", "-c", "from kerniq.telegram_listener import run_daemon_listener; run_daemon_listener()"]

    log_path = get_base_dir() / "daemon.log"
    log_file = open(log_path, "a", encoding="utf-8")

    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        proc = subprocess.Popen(
            cmd,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            cwd=str(get_base_dir())
        )
    else:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            cwd=str(get_base_dir())
        )
    log_file.close()

    # Save PID
    pid_file = get_pid_file()
    pid_file.write_text(str(proc.pid), encoding="utf-8")

    # Give process a moment to initialize and verify alive
    time.sleep(0.5)
    if proc.poll() is not None:
        pid_file.unlink(missing_ok=True)
        err_msg = ""
        try:
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                err_msg = ": " + lines[-1] if lines else ""
        except Exception:
            pass
        return False, f"Failed to start background listener (process exited with code {proc.returncode}{err_msg})."

    return True, f"Telegram listener started in background (PID {proc.pid})."

def stop_listener_daemon() -> Tuple[bool, str]:
    """Stop the running Telegram listener daemon."""
    pid = get_listener_daemon_pid()
    pid_file = get_pid_file()

    if not pid:
        pid_file.unlink(missing_ok=True)
        return False, "Telegram listener is not running."

    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except psutil.TimeoutExpired:
            proc.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    pid_file.unlink(missing_ok=True)
    return True, f"Telegram listener stopped (PID {pid})."
