"""Unit tests for terminal formatting, ANSI colors, NO_COLOR, and sanitization."""

import io
from kerniq.terminal import (
    CYAN,
    GREEN,
    RED,
    YELLOW,
    colorize,
    cyan,
    gray,
    green,
    is_color_enabled,
    red,
    sanitize_terminal_output,
    status_badge,
    yellow,
)

def test_no_color_environment(monkeypatch):
    stream = io.StringIO()
    # Mock isatty to True
    stream.isatty = lambda: True

    monkeypatch.setenv("NO_COLOR", "1")
    assert is_color_enabled(stream) is False
    assert green("test", stream) == "test"

    monkeypatch.delenv("NO_COLOR")
    assert is_color_enabled(stream) is True
    assert GREEN in green("test", stream)

def test_non_tty_stream_disables_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = io.StringIO()  # isatty is False by default
    assert is_color_enabled(stream) is False
    assert cyan("Kerniq", stream) == "Kerniq"

def test_semantic_status_badges():
    stream = io.StringIO()
    stream.isatty = lambda: True

    badge_ok = status_badge("completed", stream)
    assert "[COMPLETED]" in badge_ok

    badge_fail = status_badge("failed", stream)
    assert "[FAILED]" in badge_fail

    badge_warn = status_badge("paused", stream)
    assert "[PAUSED]" in badge_warn

def test_sanitize_terminal_output():
    # Attempting to change terminal title via OSC sequence: \x1b]0;Evil Title\x07
    dangerous_input = "\x1b]0;Hacked Title\x07Hello \x1b[31mRed\x1b[0m World\x1b[2J"
    sanitized = sanitize_terminal_output(dangerous_input)

    assert "Hacked Title" not in sanitized or "\x1b" not in sanitized
    assert "Hello" in sanitized
    assert "Red" in sanitized
    assert "World" in sanitized
    assert "\x1b" not in sanitized
