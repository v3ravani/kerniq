"""Notification dispatcher for task events and resource alerts."""

import logging
from typing import Any, Dict, List, Optional, Tuple
from kerniq.authorization import can_control, can_read, can_receive_notifications
from kerniq.config import get_rules, get_telegram_token, get_users
from kerniq.telegram_client import TelegramClient

logger = logging.getLogger("kerniq.notifications")

class NotificationService:
    """Dispatches notifications to authorized Telegram accounts based on configured rules."""

    def __init__(self, paused: bool = False, listener_active: bool = False):
        self.paused = paused
        self.listener_active = listener_active
        self._client: Optional[TelegramClient] = None

    def _get_client(self) -> Optional[TelegramClient]:
        token = get_telegram_token()
        if not token:
            return None
        if self._client is None or self._client.token != token:
            self._client = TelegramClient(token)
        return self._client

    def send_notification(
        self,
        event_name: str,
        message: str,
        task_id: Optional[str] = None,
        include_terminate_button: bool = False,
        include_output_button: bool = False,
        include_stop_button: bool = False
    ) -> List[Dict[str, Any]]:
        """Broadcast a notification to eligible authorized users.
        
        Returns a list of dispatch results per user.
        Never raises exceptions to ensure process supervision continues uninterrupted.
        """
        results = []
        rules = get_rules()

        # Send notifications only if persistent listener is on or in kerniq shell
        from kerniq.daemon import get_listener_daemon_pid
        from kerniq.storage import is_shell_active

        is_active_env = (
            self.listener_active or
            bool(get_listener_daemon_pid()) or
            is_shell_active()
        )
        if not is_active_env:
            return [{"status": "skipped", "reason": "environment_inactive"}]

        # Check if notifications are paused
        if self.paused and event_name not in ("monitoring_resumed", "monitoring_paused"):
            return [{"status": "skipped", "reason": "notifications_paused"}]

        # Check if the specific rule is enabled
        rule_key = event_name
        if not rules.get(rule_key, True):
            return [{"status": "skipped", "reason": f"rule_{rule_key}_disabled"}]

        client = self._get_client()
        if not client:
            return [{"status": "skipped", "reason": "telegram_not_configured"}]

        users = get_users()
        for u in users:
            uid = int(u.get("user_id", -1))
            if not can_receive_notifications(uid):
                continue

            # Build inline keyboard buttons specific to recipient's permissions
            keyboard = []
            row = []

            # Stop button (only if recipient has control permission)
            if (include_terminate_button or include_stop_button) and task_id and can_control(uid):
                row.append({"text": "🛑 Stop", "callback_data": f"stop:{task_id}"})

            # Output button (only if recipient has read permission)
            if include_output_button and task_id and can_read(uid):
                row.append({"text": "📄 Output", "callback_data": f"out:{task_id}"})

            if row:
                keyboard.append(row)

            reply_markup = {"inline_keyboard": keyboard} if keyboard else None

            try:
                client.send_message(
                    chat_id=uid,
                    text=message,
                    reply_markup=reply_markup
                )
                results.append({"user_id": uid, "status": "sent"})
            except Exception as e:
                # Telegram errors should be recorded, never crashing supervisor
                results.append({"user_id": uid, "status": "failed", "error": str(e)})

        return results

    # -----------------------------------------------------------------------
    # Convenience Notification Methods
    # -----------------------------------------------------------------------

    def notify_task_started(self, task_id: str, command: str, start_time: str) -> None:
        cmd_preview = command if len(command) <= 60 else command[:57] + "..."
        msg = (
            f"🚀 [Kerniq] Task Started\n\n"
            f"• Task ID: {task_id}\n"
            f"• Command: {cmd_preview}\n"
            f"• Started: {start_time}"
        )
        self.send_notification(
            event_name="task_started",
            message=msg,
            task_id=task_id,
            include_terminate_button=True,
            include_output_button=True
        )

    def notify_task_completed(self, task_id: str, command: str, duration_str: str, stdout_size: int, stderr_size: int) -> None:
        cmd_preview = command if len(command) <= 60 else command[:57] + "..."
        msg = (
            f"✅ [Kerniq] Task Completed (Exit: 0)\n\n"
            f"• Task ID: {task_id}\n"
            f"• Command: {cmd_preview}\n"
            f"• Duration: {duration_str}\n"
            f"• Output: stdout {stdout_size} B, stderr {stderr_size} B"
        )
        self.send_notification(
            event_name="task_completed",
            message=msg,
            task_id=task_id,
            include_output_button=True
        )

    def notify_task_failed(self, task_id: str, command: str, duration_str: str, exit_code: Optional[int], error_msg: Optional[str] = None) -> None:
        cmd_preview = command if len(command) <= 60 else command[:57] + "..."
        code_str = str(exit_code) if exit_code is not None else "Launch Failure"
        msg = (
            f"❌ [Kerniq] Task Failed (Exit: {code_str})\n\n"
            f"• Task ID: {task_id}\n"
            f"• Command: {cmd_preview}\n"
            f"• Duration: {duration_str}"
        )
        if error_msg:
            msg += f"\n• Error: {error_msg}"

        self.send_notification(
            event_name="task_failed",
            message=msg,
            task_id=task_id,
            include_output_button=True
        )

    def notify_task_input_required(self, task_id: str, command: str) -> None:
        cmd_preview = command if len(command) <= 60 else command[:57] + "..."
        msg = (
            f"⌨️ [Kerniq] Task Requires Input\n\n"
            f"• Task ID: {task_id}\n"
            f"• Command: {cmd_preview}\n"
            f"The task appears to be waiting for user input on the host terminal."
        )
        self.send_notification(
            event_name="task_input_required",
            message=msg,
            task_id=task_id,
            include_output_button=True,
            include_stop_button=True
        )

    def notify_monitoring_started(self, mode: str = "interactive") -> None:
        msg = f"🟢 [Kerniq] Task monitoring session started ({mode})."
        self.send_notification(event_name="monitoring_started", message=msg)

    def notify_monitoring_ended(self) -> None:
        msg = "⚪ [Kerniq] Task monitoring session ended."
        self.send_notification(event_name="monitoring_ended", message=msg)

    def notify_monitoring_paused(self) -> None:
        msg = "⏸️ [Kerniq] Task notifications paused."
        self.send_notification(event_name="monitoring_paused", message=msg)

    def notify_monitoring_resumed(self) -> None:
        msg = "▶️ [Kerniq] Task notifications resumed."
        self.send_notification(event_name="monitoring_resumed", message=msg)

    def notify_duration_alert(self, task_id: str, duration_sec: float, threshold_sec: float) -> None:
        msg = (
            f"⏱️ [Kerniq Alert] Task Duration Threshold Exceeded\n\n"
            f"• Task ID: {task_id}\n"
            f"• Duration: {duration_sec:.1f}s (Threshold: {threshold_sec:.1f}s)"
        )
        self.send_notification(
            event_name="duration_threshold",
            message=msg,
            task_id=task_id,
            include_output_button=True,
            include_stop_button=True
        )

    def notify_cpu_alert(self, task_id: str, alert_details: str) -> None:
        msg = f"⚠️ [Kerniq Alert] CPU Threshold Exceeded\n\n• Task ID: {task_id}\n• {alert_details}"
        self.send_notification(
            event_name="cpu_threshold",
            message=msg,
            task_id=task_id,
            include_output_button=True,
            include_stop_button=True
        )

    def notify_battery_alert(self, battery_percent: float, threshold: float) -> None:
        msg = (
            f"🪫 [Kerniq Alert] Low Battery\n\n"
            f"• Battery Level: {battery_percent:.1f}%\n"
            f"• Threshold: {threshold:.1f}%"
        )
        self.send_notification(event_name="battery_threshold", message=msg)

    def notify_device_registered(self, device_name: str, device_id: str) -> None:
        msg = (
            f"💻 [Kerniq] New Device Registered\n\n"
            f"• Device Name: {device_name}\n"
            f"• Device ID: {device_id}\n"
            f"This device has been registered in the local Kerniq configuration."
        )
        # Device registration notification is sent to notification recipients
        self.send_notification(event_name="monitoring_started", message=msg)

    def notify_user_added(self, user_id: int, permission: str, device_name: Optional[str] = None) -> Tuple[bool, str]:
        """Send a welcome notification to a newly added authorized user."""
        import socket
        dev = device_name or socket.gethostname()
        msg = (
            f"🎉 [Kerniq] You have been added!\n\n"
            f"You have been authorized on this device ({dev}) with permission: {permission}.\n"
            f"This device ({dev}) has been added to your Kerniq monitoring network."
        )
        client = self._get_client()
        if not client:
            return False, "Telegram is not configured."
        try:
            client.send_message(chat_id=user_id, text=msg)
            return True, "Notification sent."
        except Exception as e:
            return False, str(e)
