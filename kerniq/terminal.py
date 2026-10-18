"""Terminal styling, ANSI colors, and output safety utilities."""

import os
import re
import sys

# Ensure UTF-8 output encoding on Windows console
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure") and sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure") and sys.stderr:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Semantic color codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Semantic palette as per PRD Section 7.1
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
GRAY = "\033[90m"

def is_color_enabled(stream=None) -> bool:
    """Determine whether color output should be enabled.
    
    Checks:
    - NO_COLOR environment variable (https://no-color.org)
    - KERNIQ_NO_COLOR environment variable
    - Stream isatty check
    - Windows console color support
    """
    if os.environ.get("NO_COLOR") or os.environ.get("KERNIQ_NO_COLOR"):
        return False
    if stream is None:
        stream = sys.stdout
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    # On Windows, try enabling virtual terminal processing if needed
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # STD_OUTPUT_HANDLE = -11
            h_out = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(h_out, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                kernel32.SetConsoleMode(h_out, mode.value | 0x0004)
        except Exception:
            pass
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    return True

def colorize(text: str, color_code: str, stream=None) -> str:
    """Apply ANSI color if enabled, otherwise return plain text."""
    if is_color_enabled(stream):
        return f"{color_code}{text}{RESET}"
    return text

def cyan(text: str, stream=None) -> str:
    """Format text in cyan (Product identity, section headings, task IDs)."""
    return colorize(text, CYAN, stream)

def green(text: str, stream=None) -> str:
    """Format text in green (Confirmed success, exit code 0, verified state)."""
    return colorize(text, GREEN, stream)

def yellow(text: str, stream=None) -> str:
    """Format text in yellow (Warning, paused, threshold alert, truncated output)."""
    return colorize(text, YELLOW, stream)

def red(text: str, stream=None) -> str:
    """Format text in red (Error, non-zero exit, permission denial, invalid config)."""
    return colorize(text, RED, stream)

def blue(text: str, stream=None) -> str:
    """Format text in blue (Informational labels, secondary headings)."""
    return colorize(text, BLUE, stream)

def gray(text: str, stream=None) -> str:
    """Format text in gray/dim (Secondary info, timestamps, durations, paths)."""
    return colorize(text, GRAY, stream)

def bold(text: str, stream=None) -> str:
    """Format text in bold."""
    return colorize(text, BOLD, stream)

def status_badge(status: str, stream=None) -> str:
    """Format a semantic status badge readable with or without color."""
    upper = status.upper()
    text = f"[{upper}]"
    if upper in ("COMPLETED", "READY", "CONNECTED", "PASS", "SUCCESS", "RUNNING", "ACTIVE"):
        return green(text, stream)
    elif upper in ("WARNING", "WARN", "PAUSED", "STARTING", "TERMINATING"):
        return yellow(text, stream)
    elif upper in ("FAILED", "ERROR", "TERMINATED", "FAIL", "DENIED"):
        return red(text, stream)
    elif upper in ("INTERRUPTED", "UNKNOWN", "SKIP", "NOT_CONFIGURED", "OFFLINE"):
        return gray(text, stream)
    return text

def align_badge(status: str, width: int = 14, stream=None) -> str:
    """Format and pad a status badge accurately."""
    upper = status.upper()
    text = f"[{upper}]"
    padded = f"{text:<{width}}"
    if upper in ("COMPLETED", "READY", "CONNECTED", "PASS", "SUCCESS", "RUNNING", "ACTIVE"):
        return green(padded, stream)
    elif upper in ("WARNING", "WARN", "PAUSED", "STARTING", "TERMINATING"):
        return yellow(padded, stream)
    elif upper in ("FAILED", "ERROR", "TERMINATED", "FAIL", "DENIED"):
        return red(padded, stream)
    elif upper in ("INTERRUPTED", "UNKNOWN", "SKIP", "NOT_CONFIGURED", "OFFLINE"):
        return gray(padded, stream)
    return padded

# Regex for stripping dangerous ANSI sequences while preserving normal text
# Matches OSC (Operating System Command) sequences, CSI (Control Sequence Introducer)
# and dangerous control sequences that could manipulate terminal title or screen.
ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1B\\))"
)

def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    if not text:
        return ""
    return ANSI_ESCAPE_PATTERN.sub("", text)

def sanitize_terminal_output(text: str) -> str:
    """Sanitize untrusted output by removing dangerous terminal control sequences.
    
    Neutralizes terminal title modifications, cursor moves, screen clears,
    and escape codes that could spoof the user interface.
    """
    if not text:
        return ""
    # Strip dangerous ANSI escapes
    sanitized = ANSI_ESCAPE_PATTERN.sub("", text)
    # Filter non-printable ASCII control characters except \n, \r, \t
    cleaned_chars = []
    for ch in sanitized:
        code = ord(ch)
        if code in (9, 10, 13) or code >= 32:
            cleaned_chars.append(ch)
        else:
            # Replace unexpected control characters with safe visual indicator
            cleaned_chars.append(f"\\x{code:02x}")
    return "".join(cleaned_chars)
