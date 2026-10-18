"""Telegram Bot API client with secret redaction and error resilience."""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
from requests.exceptions import RequestException

from kerniq.exceptions import TelegramError

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

def redact_token(text: str, token: Optional[str] = None) -> str:
    """Ensure bot tokens are never leaked into error messages or exception strings."""
    if not text:
        return ""
    if token and token in text:
        text = text.replace(token, "[REDACTED_BOT_TOKEN]")
    # Also match general Telegram bot token pattern (e.g. 123456789:ABCdefGHI...)
    token_pattern = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
    return token_pattern.sub("[REDACTED_BOT_TOKEN]", text)

class TelegramClient:
    """Synchronous, resilient client for Telegram Bot API."""

    def __init__(self, token: str, timeout: int = 15):
        self.token = token.strip() if token else ""
        self.timeout = timeout
        self.session = requests.Session()

    @property
    def base_url(self) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self.token}"

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        retries: int = 2
    ) -> Dict[str, Any]:
        """Execute HTTP request to Telegram Bot API with retry and token redaction."""
        if not self.token:
            raise TelegramError("Telegram bot token is not configured.")

        url = f"{self.base_url}/{endpoint}"
        last_error = None

        for attempt in range(retries + 1):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=data,
                    files=files,
                    timeout=self.timeout
                )

                # Check for rate limiting
                if resp.status_code == 429:
                    retry_after = 5
                    try:
                        resp_json = resp.json()
                        retry_after = resp_json.get("parameters", {}).get("retry_after", 5)
                    except Exception:
                        pass
                    if attempt < retries:
                        time.sleep(retry_after)
                        continue
                    raise TelegramError(
                        f"Telegram rate limit exceeded. Retry after {retry_after}s.",
                        status_code=429,
                        retry_after=retry_after
                    )

                try:
                    payload = resp.json()
                except Exception:
                    payload = {"ok": False, "description": f"HTTP {resp.status_code}: Invalid JSON response"}

                if not payload.get("ok"):
                    desc = redact_token(payload.get("description", "Unknown Telegram API error"), self.token)
                    raise TelegramError(f"Telegram API error: {desc}", status_code=resp.status_code)

                return payload.get("result", {})

            except RequestException as e:
                clean_err = redact_token(str(e), self.token)
                last_error = TelegramError(f"Network error communicating with Telegram: {clean_err}")
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue

        if last_error:
            raise last_error
        raise TelegramError("Telegram request failed after retries.")

    def get_me(self) -> Dict[str, Any]:
        """Validate token and retrieve bot identity details."""
        return self._request("GET", "getMe")

    def set_my_commands(self) -> bool:
        """Register the official Telegram commands so they sync to the bot UI menu."""
        commands = [
            {"command": "history", "description": "Display recent task execution history"},
            {"command": "status", "description": "Display device, listener, and shell status"},
            {"command": "running", "description": "Display currently active supervised tasks"},
            {"command": "report", "description": "Full task report with download: /report <id>"},
            {"command": "devices", "description": "Display known device identities"},
            {"command": "rules", "description": "Display notification rules and thresholds"},
            {"command": "help", "description": "Display supported Telegram commands"},
            {"command": "output", "description": "Retrieve task output: /output <id>"},
            {"command": "stop", "description": "Terminate running task: /stop <id>"},
            {"command": "about", "description": "Display version and developer information"},
            {"command": "tree", "description": "Display process tree: /tree <id>"},
            {"command": "events", "description": "Display process events: /events <id>"},
            {"command": "consumption", "description": "Display resource usage: /consumption <id>"}
        ]
        try:
            self._request("POST", "setMyCommands", data={"commands": json.dumps(commands)})
            return True
        except Exception:
            return False

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send a text message to a user, bounding length to Telegram limits."""
        # Truncate if message exceeds Telegram limit
        if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH:
            suffix = "\n\n... [output bounded for Telegram; use 'kerniq output <ID>' for full log]"
            text = text[: (TELEGRAM_MAX_MESSAGE_LENGTH - len(suffix))] + suffix

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        if parse_mode:
            payload["parse_mode"] = parse_mode

        return self._request("POST", "sendMessage", data=payload)

    def send_document(
        self,
        chat_id: int,
        file_path: Path,
        caption: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send a local file as a Telegram document."""
        if not file_path.exists():
            raise TelegramError(f"File not found: {file_path}")

        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]

        with open(file_path, "rb") as f:
            files = {"document": (file_path.name, f)}
            return self._request("POST", "sendDocument", data=data, files=files)

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False
    ) -> bool:
        """Acknowledge an inline button callback query."""
        payload = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert
        }
        if text:
            payload["text"] = text[:200]
        try:
            self._request("POST", "answerCallbackQuery", data=payload)
            return True
        except Exception:
            return False

    def get_updates(self, offset: Optional[int] = None, timeout: int = 25) -> List[Dict[str, Any]]:
        """Long poll for incoming updates."""
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        old_timeout = self.timeout
        try:
            self.timeout = timeout + 10
            res = self._request("GET", "getUpdates", params=params, retries=1)
            return res if isinstance(res, list) else []
        finally:
            self.timeout = old_timeout
