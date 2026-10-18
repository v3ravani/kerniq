"""Telegram command listener and interactive callback processor."""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from kerniq.authorization import (
    PermissionLevel,
    can_control,
    can_read,
    can_receive_notifications,
    get_user_permission,
    is_authorized,
)
from kerniq.config import RULE_DEFINITIONS, get_rules, get_telegram_token, get_users
from kerniq.exceptions import TelegramError
from kerniq.storage import Storage
from kerniq.tasks import TaskSupervisor, format_duration
from kerniq.telegram_client import TelegramClient

logger = logging.getLogger("kerniq.listener")

class TelegramListener:
    """Long-polling service handling incoming Telegram commands and inline callbacks."""

    def __init__(self, supervisor: Optional[TaskSupervisor] = None, storage: Optional[Storage] = None):
        self.storage = storage or Storage()
        self.supervisor = supervisor or TaskSupervisor(self.storage)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[TelegramClient] = None
        self._last_update_id: Optional[int] = None
        self._lock = threading.Lock()
        self.started_at: Optional[str] = None
        self.started_epoch: float = time.time()

    def _get_client(self) -> Optional[TelegramClient]:
        token = get_telegram_token()
        if not token:
            return None
        if self._client is None or self._client.token != token:
            self._client = TelegramClient(token)
        return self._client

    def is_running(self) -> bool:
        return self._running

    def start_background(self) -> bool:
        """Start listener in a daemon background thread."""
        with self._lock:
            if self._running:
                return True
            client = self._get_client()
            if not client:
                return False
            try:
                client.set_my_commands()
            except Exception:
                pass
            self._running = True
            self.started_epoch = time.time()
            self.started_at = datetime.now(timezone.utc).isoformat()
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        """Stop polling listener."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def run_forever(self) -> None:
        """Run listener synchronously in foreground."""
        self._running = True
        self.started_at = datetime.now(timezone.utc).isoformat()
        client = self._get_client()
        if client:
            try:
                client.set_my_commands()
            except Exception:
                pass
        try:
            self._poll_loop()
        finally:
            self._running = False

    def _poll_loop(self) -> None:
        """Main polling loop using Telegram getUpdates."""
        while self._running:
            client = self._get_client()
            if not client:
                time.sleep(3.0)
                continue

            try:
                updates = client.get_updates(offset=self._last_update_id, timeout=15)
                for upd in updates:
                    upd_id = upd.get("update_id")
                    if upd_id is not None:
                        self._last_update_id = upd_id + 1

                    self._handle_update(upd, client)
            except TelegramError as e:
                time.sleep(2.0)
            except Exception as e:
                time.sleep(2.0)

    def _handle_update(self, update: Dict[str, Any], client: TelegramClient) -> None:
        """Route message or callback query."""
        if "message" in update:
            self._handle_message(update["message"], client)
        elif "callback_query" in update:
            self._handle_callback_query(update["callback_query"], client)

    def _handle_message(self, message: Dict[str, Any], client: TelegramClient) -> None:
        """Handle incoming text slash command or user query."""
        from_user = message.get("from", {})
        user_id = from_user.get("id")
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()

        if not user_id or not chat_id or not text:
            return

        # 1. Authorization check
        if not is_authorized(user_id):
            client.send_message(
                chat_id=chat_id,
                text="⛔ Access denied. Your Telegram account is not authorized to interact with this Kerniq instance."
            )
            return

        perm = get_user_permission(user_id)
        if perm == PermissionLevel.NOTIFICATIONS:
            client.send_message(
                chat_id=chat_id,
                text="ℹ️ Your account has notification-only permissions and cannot query tasks or trigger commands."
            )
            return

        # Check if the query was sent while Kerniq was inactive (before listener/shell was started)
        msg_date = message.get("date", 0)
        was_offline = (msg_date > 0 and (self.started_epoch - msg_date) > 5)

        # Handle non-slash query messages by providing guidance or matching query
        if not text.startswith("/"):
            lower_text = text.lower().strip()
            if lower_text == "help":
                self._cmd_help(chat_id, perm, client, was_offline=was_offline)
                return
            elif lower_text == "status":
                self._cmd_status(chat_id, client, was_offline=was_offline)
                return
            elif lower_text == "running":
                self._cmd_running(chat_id, client, was_offline=was_offline)
                return
            elif lower_text == "history":
                self._cmd_history(chat_id, client, was_offline=was_offline)
                return
            elif lower_text == "rules":
                self._cmd_rules(chat_id, client, was_offline=was_offline)
                return
            elif lower_text == "devices":
                self._cmd_devices(chat_id, client, was_offline=was_offline)
                return
            else:
                is_shell = self.storage.is_shell_active()
                shell_note = "Active" if is_shell else "Inactive"
                msg = (
                    f"Hello! I received your query: \"{text}\"\n\n"
                    f"• Interactive shell: `{shell_note}`\n"
                    f"• Persistent listener: `Active`\n\n"
                    f"Use /help to view all supported commands (/running, /history, /status, /output, /stop, /rules, /devices)."
                )
                self._send_reply(chat_id=chat_id, text=msg, client=client, was_offline=was_offline, parse_mode="Markdown")
                return

        parts = text.split()
        cmd = parts[0].lower().split("@")[0]  # Strip bot username if present: /cmd@bot
        args = parts[1:]

        if cmd == "/help":
            self._cmd_help(chat_id, perm, client, was_offline=was_offline)
        elif cmd == "/status":
            self._cmd_status(chat_id, client, was_offline=was_offline)
        elif cmd == "/running":
            self._cmd_running(chat_id, client, was_offline=was_offline)
        elif cmd == "/history":
            self._cmd_history(chat_id, client, was_offline=was_offline)
        elif cmd == "/rules":
            self._cmd_rules(chat_id, client, was_offline=was_offline)
        elif cmd == "/devices":
            self._cmd_devices(chat_id, client, was_offline=was_offline)
        elif cmd == "/output":
            self._cmd_output(chat_id, args, client, was_offline=was_offline)
        elif cmd in ("/stop", "/terminate"):
            self._cmd_stop(chat_id, user_id, args, client, was_offline=was_offline)
        elif cmd == "/about":
            self._cmd_about(chat_id, client, was_offline=was_offline)
        elif cmd == "/report":
            self._cmd_report(chat_id, args, client, was_offline=was_offline)
        elif cmd == "/tree":
            self._cmd_tree(chat_id, args, client, was_offline=was_offline)
        elif cmd == "/events":
            self._cmd_events(chat_id, args, client, was_offline=was_offline)
        elif cmd == "/consumption":
            self._cmd_consumption(chat_id, args, client, was_offline=was_offline)
        else:
            self._send_reply(
                chat_id=chat_id,
                text=f"Unknown command '{cmd}'. Supported commands: /running, /history, /help, /status, /output, /stop, /rules, /devices, /report, /about, /tree, /events, /consumption.",
                client=client,
                was_offline=was_offline
            )

    def _send_reply(self, chat_id: int, text: str, client: TelegramClient, was_offline: bool = False, parse_mode: Optional[str] = None) -> None:
        """Send message and append notice if the query arrived while Kerniq was inactive."""
        if was_offline:
            if parse_mode == "Markdown":
                text += "\n\n⚠️ _(Note: This query was sent while Kerniq was inactive; neither interactive shell nor background listener was running.)_"
            else:
                text += "\n\n⚠️ (Note: This query was sent while Kerniq was inactive; neither interactive shell nor background listener was running.)"
        try:
            client.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        except Exception:
            if parse_mode:
                try:
                    plain_text = text.replace("*", "").replace("`", "").replace("_", "")
                    client.send_message(chat_id=chat_id, text=plain_text, parse_mode=None)
                except Exception:
                    pass

    def _handle_callback_query(self, query: Dict[str, Any], client: TelegramClient) -> None:
        """Handle inline button presses (e.g. stop or view output)."""
        qid = query.get("id")
        from_user = query.get("from", {})
        user_id = from_user.get("id")
        message = query.get("message") or {}
        chat_id = message.get("chat", {}).get("id") or user_id
        data = query.get("data", "")

        if not qid:
            return

        if not user_id or not chat_id:
            client.answer_callback_query(qid, text="Unable to resolve user or chat.", show_alert=True)
            return

        # Re-verify authorization on every callback click
        if not is_authorized(user_id):
            client.answer_callback_query(qid, text="Access denied: unauthorized account.", show_alert=True)
            return

        msg_date = message.get("date", 0)
        was_offline = (msg_date > 0 and (self.started_epoch - msg_date) > 5)

        perm = get_user_permission(user_id)
        try:
            if data.startswith("stop:") or data.startswith("term:"):
                prefix = "stop:" if data.startswith("stop:") else "term:"
                task_id = data[len(prefix):].strip()
                if not can_control(user_id):
                    client.answer_callback_query(qid, text="Denied: 'control' permission required.", show_alert=True)
                    return

                task = self.storage.get_task(task_id)
                if not task:
                    client.answer_callback_query(qid, text=f"Task {task_id} not found.", show_alert=True)
                    return

                if task["status"] not in ("starting", "running", "terminating"):
                    client.answer_callback_query(qid, text=f"Task is already {task['status']}.", show_alert=True)
                    return

                client.answer_callback_query(qid, text=f"Stopping task {task_id}...")
                ok, msg = self.supervisor.terminate_task(task_id)
                emoji = "🛑" if ok else "⚠️"
                self._send_reply(chat_id=chat_id, text=f"{emoji} {msg}", client=client, was_offline=was_offline)

            elif data.startswith("out:"):
                task_id = data[4:].strip()
                if not can_read(user_id):
                    client.answer_callback_query(qid, text="Denied: 'readonly' or 'control' permission required.", show_alert=True)
                    return

                client.answer_callback_query(qid, text=f"Fetching output for {task_id}...")
                self._send_task_output(chat_id, task_id, client, was_offline=was_offline)
            elif data.startswith("tree:"):
                task_id = data[5:].strip()
                if not can_read(user_id):
                    client.answer_callback_query(qid, text="Denied: 'readonly' or 'control' permission required.", show_alert=True)
                    return
                client.answer_callback_query(qid, text=f"Fetching process tree for {task_id}...")
                self._cmd_tree(chat_id, [task_id], client, was_offline=was_offline)
            elif data.startswith("events:"):
                task_id = data[7:].strip()
                if not can_read(user_id):
                    client.answer_callback_query(qid, text="Denied: 'readonly' or 'control' permission required.", show_alert=True)
                    return
                client.answer_callback_query(qid, text=f"Fetching events for {task_id}...")
                self._cmd_events(chat_id, [task_id], client, was_offline=was_offline)
            elif data.startswith("consumption:"):
                task_id = data[12:].strip()
                if not can_read(user_id):
                    client.answer_callback_query(qid, text="Denied: 'readonly' or 'control' permission required.", show_alert=True)
                    return
                client.answer_callback_query(qid, text=f"Fetching consumption for {task_id}...")
                self._cmd_consumption(chat_id, [task_id], client, was_offline=was_offline)
            elif data.startswith("report_dl:"):
                task_id = data[10:].strip()
                if not can_read(user_id):
                    client.answer_callback_query(qid, text="Denied: 'readonly' or 'control' permission required.", show_alert=True)
                    return
                task = self.storage.get_task(task_id)
                if not task:
                    client.answer_callback_query(qid, text=f"Task {task_id} not found.", show_alert=True)
                    return
                client.answer_callback_query(qid, text=f"Generating report download for {task_id}...")
                try:
                    from kerniq.export import export_task
                    tree_data = self.storage.get_task_process_tree(task_id)
                    report_file = export_task(task, tree_data=tree_data)
                    client.send_document(
                        chat_id=chat_id,
                        file_path=report_file,
                        caption=f"📄 Kerniq Task Report - {task_id}"
                    )
                except Exception as ex:
                    self._send_reply(chat_id=chat_id, text=f"⚠️ Failed to generate or send report document: {ex}", client=client, was_offline=was_offline)
            else:
                client.answer_callback_query(qid, text="Action received.")
        except Exception as e:
            logger.exception("Error processing callback query %s", qid)
            try:
                client.answer_callback_query(qid, text=f"Error: {e}", show_alert=True)
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Command Implementations
    # -----------------------------------------------------------------------

    def _cmd_help(self, chat_id: int, perm: PermissionLevel, client: TelegramClient, was_offline: bool = False) -> None:
        lines = [
            "🛡️ *Kerniq Telegram Commands*",
            f"Your permission tier: `{perm.value}`\n",
            "• `/history` - Display recent saved task history",
            "• `/status` - Display device, listener daemon, and shell status",
            "• `/devices` - Display known device identities and status",
            "• `/rules` - Display notification rules and thresholds",
            "• `/help` - Display supported Telegram commands",
            "• `/running` - Display currently active supervised tasks",
            "• `/report <id>` - Comprehensive report (events, tree, stats) with download",
            "• `/output <id>` - Retrieve saved stdout/stderr log (or inline)",
            "• `/stop <id>` - Terminate monitored running task (or inline)",
            "• `/about` - Display version and developer information",
            "• `/tree <id>` - Display process hierarchy tree",
            "• `/events <id>` - Display chronological process lifecycle events",
            "• `/consumption <id>` - Display task resource consumption"
        ]
        self._send_reply(chat_id=chat_id, text="\n".join(lines), client=client, was_offline=was_offline, parse_mode="Markdown")

    def _cmd_status(self, chat_id: int, client: TelegramClient, was_offline: bool = False) -> None:
        from kerniq.config import get_device_info, get_effective_device_id
        from kerniq.metrics import get_battery_info

        dev_info = get_device_info()
        dev_name = dev_info.get("name", "Unknown")
        dev_id = get_effective_device_id()
        id_mode = dev_info.get("id_mode", "hostname")

        bat = get_battery_info()
        if bat.get("available") and bat.get("percent") is not None:
            bat_str = f"{bat['percent']:.0f}% ({'Plugged in' if bat.get('power_plugged') else 'Discharging'})"
        else:
            bat_str = "N/A"

        shell_active = self.storage.is_shell_active()
        shell_str = "Active" if shell_active else "Inactive"
        listener_str = "Active"
        active_count = self.storage.count_active_tasks()

        msg = (
            f"📊 *Kerniq Status*\n\n"
            f"• Device Name: `{dev_name}`\n"
            f"• Device ID ({id_mode}): `{dev_id}`\n"
            f"• Battery: `{bat_str}`\n"
            f"• Interactive Shell: `{shell_str}`\n"
            f"• Persistent Listener: `{listener_str}`\n"
            f"• Monitored Tasks: `{active_count}`\n"
            f"• Telegram Link: `Connected`"
        )
        self._send_reply(chat_id=chat_id, text=msg, client=client, was_offline=was_offline, parse_mode="Markdown")

    def _cmd_running(self, chat_id: int, client: TelegramClient, was_offline: bool = False) -> None:
        active = self.storage.get_active_tasks()
        if not active:
            self._send_reply(chat_id=chat_id, text="No Kerniq tasks are currently running.", client=client, was_offline=was_offline)
            return

        lines = [f"🏃 *Monitored Running Tasks ({len(active)})*:\n"]
        inline_rows = []
        for t in active[:10]:
            tid = t["task_id"]
            cmd = t["command"]
            if len(cmd) > 40:
                cmd = cmd[:37] + "..."
            status = t["status"]
            dur = format_duration(t.get("duration_seconds") or 0)
            lines.append(f"• `{tid}` [{status}] (elapsed: {dur}) - {cmd}")
            inline_rows.append([
                {"text": f"🛑 Stop {tid}", "callback_data": f"stop:{tid}"},
                {"text": f"📄 Output {tid}", "callback_data": f"out:{tid}"}
            ])

        if len(active) > 10:
            lines.append(f"\n_... and {len(active) - 10} more tasks._")

        reply_markup = {"inline_keyboard": inline_rows} if inline_rows else None
        try:
            client.send_message(chat_id=chat_id, text="\n".join(lines), reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            client.send_message(chat_id=chat_id, text="\n".join(lines), reply_markup=reply_markup)

    def _cmd_history(self, chat_id: int, client: TelegramClient, was_offline: bool = False) -> None:
        tasks = self.storage.list_tasks(limit=7)
        if not tasks:
            self._send_reply(chat_id=chat_id, text="No task history found.", client=client, was_offline=was_offline)
            return

        lines = ["📜 *Recent Task History:*\n"]
        for t in tasks:
            tid = t["task_id"]
            status = t["status"]
            dur = format_duration(t.get("duration_seconds") or 0)
            code = t.get("exit_code")
            code_str = f"code {code}" if code is not None else status
            start = (t.get("start_time") or "")[:19].replace("T", " ")
            lines.append(f"• `{tid}` [{status}]: {t['command'][:28]} ({code_str}, {dur}) at {start}")

        lines.append("\n_Use /output <id> to view captured logs._")
        self._send_reply(chat_id=chat_id, text="\n".join(lines), client=client, was_offline=was_offline, parse_mode="Markdown")

    def _cmd_rules(self, chat_id: int, client: TelegramClient, was_offline: bool = False) -> None:
        rules = get_rules()
        lines = [
            "⚙️ *Notification Rules & Thresholds:*\n",
            "*Notification Rules:*",
        ]
        for key, label in RULE_DEFINITIONS:
            is_thresh = "threshold" in key
            default_val = False if is_thresh else True
            val = rules.get(key, default_val)
            badge = "ENABLED" if val else "DISABLED"
            lines.append(f"• {label}: `{badge}`")

        lines.extend([
            "\n*Thresholds:*",
            f"• Battery Threshold: `{rules.get('battery_percent', 20.0)}%`",
            f"• RAM / Memory Threshold: `{rules.get('memory_mb', 1024.0)} MB`",
            f"• CPU Threshold: `{rules.get('cpu_percent', 80.0)}%`",
            f"• Duration Threshold: `{rules.get('duration_seconds', 300)}s`",
            f"• Alert Cooldown: `{rules.get('cpu_cooldown_seconds', 300)}s`",
        ])
        self._send_reply(chat_id=chat_id, text="\n".join(lines), client=client, was_offline=was_offline, parse_mode="Markdown")

    def _cmd_devices(self, chat_id: int, client: TelegramClient, was_offline: bool = False) -> None:
        from kerniq.config import get_device_info, get_effective_device_id
        dev_info = get_device_info()
        dev_name = dev_info.get("name", "Unknown")
        dev_id = get_effective_device_id()
        id_mode = dev_info.get("id_mode", "hostname")

        lines = [
            "💻 *Known Device Identities:*\n",
            f"• *{dev_name}* (Current Device)",
            f"  - Device ID ({id_mode}): `{dev_id}`",
            f"  - Status: `Online / Active (Host)`"
        ]
        self._send_reply(chat_id=chat_id, text="\n".join(lines), client=client, was_offline=was_offline, parse_mode="Markdown")

    def _cmd_output(self, chat_id: int, args: List[str], client: TelegramClient, was_offline: bool = False) -> None:
        if not args:
            self._send_reply(chat_id=chat_id, text="Usage: `/output <id>`", client=client, was_offline=was_offline, parse_mode="Markdown")
            return
        task_id = args[0].strip()
        self._send_task_output(chat_id, task_id, client, was_offline=was_offline)

    def _send_task_output(self, chat_id: int, task_id: str, client: TelegramClient, was_offline: bool = False) -> None:
        task = self.storage.get_task(task_id)
        if not task:
            self._send_reply(chat_id=chat_id, text=f"Task '{task_id}' not found.", client=client, was_offline=was_offline, parse_mode=None)
            return

        out_path = Path(task.get("stdout_path", "")) if task.get("stdout_path") else None
        err_path = Path(task.get("stderr_path", "")) if task.get("stderr_path") else None

        content = []
        if out_path and out_path.exists() and out_path.stat().st_size > 0:
            try:
                with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    tail_lines = lines[-25:] if len(lines) > 25 else lines
                    content.append("--- stdout (recent) ---\n" + "".join(tail_lines))
            except Exception as e:
                content.append(f"Could not read stdout: {e}")

        if err_path and err_path.exists() and err_path.stat().st_size > 0:
            try:
                with open(err_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    tail_lines = lines[-25:] if len(lines) > 25 else lines
                    content.append("--- stderr (recent) ---\n" + "".join(tail_lines))
            except Exception as e:
                content.append(f"Could not read stderr: {e}")

        if not content:
            cmd_preview = task.get("command", "")
            status = task.get("status", "unknown")
            dur = format_duration(task.get("duration_seconds") or 0)
            self._send_reply(
                chat_id=chat_id,
                text=f"📄 Output for Task {task_id}:\n(No captured stdout/stderr recorded or files expired)\n\n• Command: {cmd_preview}\n• Status: {status}\n• Duration: {dur}",
                client=client,
                was_offline=was_offline,
                parse_mode=None
            )
            return

        header = f"📄 Output for Task {task_id}:\n" + ("=" * 35) + "\n"
        text = header + "\n".join(content)
        self._send_reply(chat_id=chat_id, text=text, client=client, was_offline=was_offline, parse_mode=None)

    def _cmd_stop(self, chat_id: int, user_id: int, args: List[str], client: TelegramClient, was_offline: bool = False) -> None:
        if not can_control(user_id):
            self._send_reply(chat_id=chat_id, text="⛔ Permission denied. Task termination requires 'control' tier.", client=client, was_offline=was_offline)
            return

        if not args:
            self._send_reply(chat_id=chat_id, text="Usage: `/stop <id>`", client=client, was_offline=was_offline, parse_mode="Markdown")
            return

        task_id = args[0].strip()
        task = self.storage.get_task(task_id)
        if not task:
            self._send_reply(chat_id=chat_id, text=f"⚠️ Task '{task_id}' not found.", client=client, was_offline=was_offline)
            return

        if task["status"] not in ("starting", "running", "terminating"):
            self._send_reply(chat_id=chat_id, text=f"⚠️ Task '{task_id}' is already {task['status']}.", client=client, was_offline=was_offline)
            return

        ok, msg = self.supervisor.terminate_task(task_id)
        emoji = "🛑" if ok else "⚠️"
        self._send_reply(chat_id=chat_id, text=f"{emoji} {msg}", client=client, was_offline=was_offline)

    def _cmd_about(self, chat_id: int, client: TelegramClient, was_offline: bool = False) -> None:
        import kerniq
        lines = [
            f"⚡ *Kerniq {kerniq.__version__}*",
            "Local task monitoring with Telegram notifications\n",
            "• Developed by: *Viraj Ravani*",
            "• Project: [github.com/v3ravani/kerniq](https://github.com/v3ravani/kerniq)"
        ]
        self._send_reply(chat_id=chat_id, text="\n".join(lines), client=client, was_offline=was_offline, parse_mode="Markdown")

    def _cmd_report(self, chat_id: int, args: List[str], client: TelegramClient, was_offline: bool = False) -> None:
        if not args:
            self._send_reply(chat_id=chat_id, text="Usage: `/report <id>`", client=client, was_offline=was_offline, parse_mode="Markdown")
            return

        task_id = args[0].strip()
        task = self.storage.get_task(task_id)
        if not task:
            self._send_reply(chat_id=chat_id, text=f"⚠️ Task '{task_id}' not found.", client=client, was_offline=was_offline)
            return

        cmd = task.get("command", "")
        status = task.get("status", "").upper()
        code = task.get("exit_code")
        code_str = str(code) if code is not None else "running"
        dur = format_duration(task.get("duration_seconds") or 0)
        start = (task.get("start_time") or "")[:19].replace("T", " ")
        end = (task.get("end_time") or "")[:19].replace("T", " ") if task.get("end_time") else "in progress"

        tree_data = self.storage.get_task_process_tree(task_id)
        from kerniq.proc_tree import format_process_events, format_resource_consumption, render_process_tree, update_live_tree
        from kerniq.terminal import strip_ansi

        if task.get("status") == "running" and task.get("pid"):
            tree_data = update_live_tree(task["pid"], tree_data, task_id)

        tree_str = strip_ansi(render_process_tree(task, tree_data))
        events_str = strip_ansi(format_process_events(task, tree_data))
        consumption_str = strip_ansi(format_resource_consumption(task, tree_data))

        out_tail = ""
        out_path = task.get("stdout_path")
        if out_path and Path(out_path).exists():
            try:
                raw_lines = Path(out_path).read_text(encoding="utf-8", errors="replace").splitlines()
                if raw_lines:
                    out_tail = "\n".join(raw_lines[-12:])
            except Exception:
                pass

        rep_lines = [
            f"📋 *Task Report - {task_id}*\n",
            f"• *Command:* `{cmd[:60]}`",
            f"• *Status:* `{status}` (Exit Code: `{code_str}`)",
            f"• *Start Time:* `{start}`",
            f"• *End Time:* `{end}`",
            f"• *Duration:* `{dur}`\n",
            f"*Process Tree:*",
            f"```\n{tree_str[:350]}\n```\n",
            f"*Resource Consumption:*",
            f"```\n{consumption_str}\n```\n",
            f"*Process Events:*",
            f"```\n{events_str[:350]}\n```",
        ]
        if out_tail:
            rep_lines.extend([
                f"\n*Output Log Excerpt:*",
                f"```\n{strip_ansi(out_tail)[:500]}\n```"
            ])

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "📥 Download Report", "callback_data": f"report_dl:{task_id}"},
                    {"text": "🌲 Process Tree", "callback_data": f"tree:{task_id}"}
                ]
            ]
        }
        try:
            client.send_message(
                chat_id=chat_id,
                text="\n".join(rep_lines),
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        except Exception:
            client.send_message(chat_id=chat_id, text=strip_ansi("\n".join(rep_lines)), reply_markup=reply_markup)

    def _cmd_tree(self, chat_id: int, args: List[str], client: TelegramClient, was_offline: bool = False) -> None:
        if not args:
            self._send_reply(chat_id=chat_id, text="Usage: `/tree <id>`", client=client, was_offline=was_offline, parse_mode="Markdown")
            return
        task_id = args[0].strip()
        task = self.storage.get_task(task_id)
        if not task:
            self._send_reply(chat_id=chat_id, text=f"⚠️ Task '{task_id}' not found.", client=client, was_offline=was_offline)
            return
        tree_data = self.storage.get_task_process_tree(task_id)
        from kerniq.proc_tree import render_process_tree, update_live_tree
        from kerniq.terminal import strip_ansi
        if task.get("status") == "running" and task.get("pid"):
            tree_data = update_live_tree(task["pid"], tree_data, task_id)
        rendered = render_process_tree(task, tree_data)
        plain = strip_ansi(rendered)
        self._send_reply(chat_id=chat_id, text=f"```\n{plain}\n```", client=client, was_offline=was_offline, parse_mode="Markdown")

    def _cmd_events(self, chat_id: int, args: List[str], client: TelegramClient, was_offline: bool = False) -> None:
        if not args:
            self._send_reply(chat_id=chat_id, text="Usage: `/events <id>`", client=client, was_offline=was_offline, parse_mode="Markdown")
            return
        task_id = args[0].strip()
        task = self.storage.get_task(task_id)
        if not task:
            self._send_reply(chat_id=chat_id, text=f"⚠️ Task '{task_id}' not found.", client=client, was_offline=was_offline)
            return
        tree_data = self.storage.get_task_process_tree(task_id)
        from kerniq.proc_tree import format_process_events
        from kerniq.terminal import strip_ansi
        rendered = format_process_events(task, tree_data)
        plain = strip_ansi(rendered)
        self._send_reply(chat_id=chat_id, text=f"```\n{plain}\n```", client=client, was_offline=was_offline, parse_mode="Markdown")

    def _cmd_consumption(self, chat_id: int, args: List[str], client: TelegramClient, was_offline: bool = False) -> None:
        if not args:
            self._send_reply(chat_id=chat_id, text="Usage: `/consumption <id>`", client=client, was_offline=was_offline, parse_mode="Markdown")
            return
        task_id = args[0].strip()
        task = self.storage.get_task(task_id)
        if not task:
            self._send_reply(chat_id=chat_id, text=f"⚠️ Task '{task_id}' not found.", client=client, was_offline=was_offline)
            return
        tree_data = self.storage.get_task_process_tree(task_id)
        from kerniq.proc_tree import format_resource_consumption
        from kerniq.terminal import strip_ansi
        rendered = format_resource_consumption(task, tree_data)
        plain = strip_ansi(rendered)
        self._send_reply(chat_id=chat_id, text=f"```\n{plain}\n```", client=client, was_offline=was_offline, parse_mode="Markdown")


def run_daemon_listener() -> None:
    """Entry point for the persistent background listener daemon process."""
    import signal
    import sys
    listener = TelegramListener()

    def _sig_handler(signum, frame):
        listener.stop()
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:
        pass
    try:
        signal.signal(signal.SIGINT, _sig_handler)
    except Exception:
        pass

    listener.run_forever()
