"""Unit tests for Telegram client, secret redaction, and bounded messaging."""

import pytest
from kerniq.exceptions import TelegramError
from kerniq.telegram_client import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TelegramClient,
    redact_token,
)

def test_token_redaction():
    secret_token = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_01234567"
    raw_error = f"Connection failed to api.telegram.org/bot{secret_token}/sendMessage"
    redacted = redact_token(raw_error, secret_token)

    assert secret_token not in redacted
    assert "[REDACTED_BOT_TOKEN]" in redacted

def test_message_length_bounding():
    client = TelegramClient("dummy_token")
    huge_text = "A" * (TELEGRAM_MAX_MESSAGE_LENGTH + 500)

    # Mock the internal _request method to inspect payload
    sent_payload = {}
    def mock_request(method, endpoint, params=None, data=None, files=None, retries=2):
        nonlocal sent_payload
        sent_payload = data
        return {"message_id": 1}

    client._request = mock_request
    client.send_message(chat_id=123, text=huge_text)

    assert len(sent_payload["text"]) <= TELEGRAM_MAX_MESSAGE_LENGTH
    assert "bounded for Telegram" in sent_payload["text"]

def test_missing_token_raises():
    client = TelegramClient("")
    with pytest.raises(TelegramError) as exc_info:
        client.get_me()
    assert "not configured" in str(exc_info.value)

def test_set_my_commands():
    client = TelegramClient("dummy_token")
    sent = {}
    def mock_request(method, endpoint, params=None, data=None, files=None, retries=2):
        sent["method"] = method
        sent["endpoint"] = endpoint
        sent["data"] = data
        return True

    client._request = mock_request
    ok = client.set_my_commands()
    assert ok is True
    assert sent["endpoint"] == "setMyCommands"
    assert "help" in sent["data"]["commands"]
    assert "stop" in sent["data"]["commands"]

def test_answer_callback_query():
    client = TelegramClient("dummy_token")
    sent = {}
    def mock_request(method, endpoint, params=None, data=None, files=None, retries=2):
        sent["endpoint"] = endpoint
        sent["data"] = data
        return True

    client._request = mock_request
    ok = client.answer_callback_query("qid_456", text="Acknowledged", show_alert=False)
    assert ok is True
    assert sent["endpoint"] == "answerCallbackQuery"
    assert sent["data"]["callback_query_id"] == "qid_456"
    assert sent["data"]["text"] == "Acknowledged"

