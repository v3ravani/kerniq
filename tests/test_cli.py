"""Tests for Kerniq CLI command dispatch, arguments, and exit codes."""

import io
import sys
from unittest.mock import patch
import pytest

from kerniq.cli import main
from kerniq.config import get_users

@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    from kerniq.config import delete_telegram_token
    delete_telegram_token()
    yield tmp_path
    delete_telegram_token()

def setup_dummy_telegram_and_user():
    from kerniq.config import set_telegram_token, add_user
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    add_user(1001, "control")

def test_cli_bare_invocation(capsys):
    ret = main([])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Kerniq" in captured.out
    assert "Local task monitoring" in captured.out

def test_cli_version(capsys):
    ret = main(["version"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Kerniq version" in captured.out

def test_cli_help(capsys):
    ret = main(["help"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Command Reference" in captured.out
    assert "record -- <command>" in captured.out
    assert "kerniq <id>" in captured.out
    assert "kerniq shell" in captured.out
    assert "--telegram" in captured.out
    assert "--shell" in captured.out

def test_cli_status(capsys):
    ret = main(["status"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Kerniq System Status" in captured.out

def test_cli_doctor(capsys):
    ret = main(["doctor"])
    captured = capsys.readouterr()
    assert "Kerniq Diagnostic Suite" in captured.out
    # Doctor may succeed or warn about missing telegram token, which is normal on clean install
    assert ret in (0, 1)

def test_cli_config_users_lifecycle(capsys):
    # Add user
    ret = main(["config users", "add", "--user-id", "123456", "--permission", "control"])
    assert ret == 0
    assert len(get_users()) == 1

    # Show users
    ret = main(["show users", "--reveal"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "123456" in captured.out
    assert "control" in captured.out

    # Remove user
    ret = main(["config users", "remove", "--user-id", "123456"])
    assert ret == 0
    assert len(get_users()) == 0

def test_cli_config_and_show_rules(capsys):
    ret = main(["config rules", "--cpu-percent", "88.0", "--cooldown", "120"])
    assert ret == 0

    ret = main(["show rules"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "88.0" in captured.out
    assert "120" in captured.out

def test_cli_record_command(capsys):
    setup_dummy_telegram_and_user()
    ret = main(["record", "--", sys.executable, "-c", "print('CLI record works'); import sys; sys.exit(0)"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "CLI record works" in captured.out
    assert "[OK] Task" in captured.out

def test_cli_history_and_output(capsys):
    setup_dummy_telegram_and_user()
    # Run a command first
    main(["record", "--", sys.executable, "-c", "print('output_test_marker')"])
    
    # View history
    ret = main(["history", "--limit", "5"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Saved Task History" in captured.out

    # Extract task ID from history
    import re
    match = re.search(r"KQ-[0-9A-Fa-f]+", captured.out)
    assert match is not None
    tid = match.group(0)

    # View output for task ID
    ret = main(["output", tid])
    assert ret == 0
    captured_out = capsys.readouterr()
    assert "output_test_marker" in captured_out.out

def test_cli_cleanup(capsys):
    setup_dummy_telegram_and_user()
    main(["record", "--", sys.executable, "-c", "print('cleanup_task')"])
    ret = main(["cleanup"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Cleanup completed" in captured.out

def test_cli_keyboard_interrupt_handled_gracefully(capsys):
    with patch("getpass.getpass", side_effect=KeyboardInterrupt):
        ret = main(["config telegram"])
        assert ret == 130
        captured = capsys.readouterr()
        assert "Operation cancelled" in captured.out

def test_cli_config_users_welcome_notification(capsys):
    from unittest.mock import MagicMock
    with patch("kerniq.notifications.NotificationService.notify_user_added", return_value=(True, "Notification sent.")):
        ret = main(["config users", "--user-id", "987654", "--permission", "readonly"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Authorized user 987654" in captured.out
        assert "Welcome notification sent to Telegram user 987654" in captured.out

def test_cli_config_users_interactive_add_and_permission_change(capsys):
    # Test interactive flow: Option 1 (Add user), ID 555444, Option 1 (control)
    inputs = iter(["1", "555444", "1"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        with patch("kerniq.notifications.NotificationService.notify_user_added", return_value=(True, "Sent.")):
            ret = main(["config users"])
            assert ret == 0
            assert any(u["user_id"] == 555444 and u["permission"] == "control" for u in get_users())

    # Test interactive flow: Option 2 (Change user permission), Option 2 (readonly)
    inputs_change = iter(["2", "2"])
    with patch("builtins.input", lambda prompt="": next(inputs_change)):
        ret = main(["config users"])
        assert ret == 0
        assert any(u["user_id"] == 555444 and u["permission"] == "readonly" for u in get_users())

    # Test interactive flow: Option 3 (Remove user)
    inputs_remove = iter(["3", "555444"])
    with patch("builtins.input", lambda prompt="": next(inputs_remove)):
        ret = main(["config users"])
        assert ret == 0
        assert not any(u["user_id"] == 555444 for u in get_users())

def test_cli_record_command_flag(capsys):
    setup_dummy_telegram_and_user()
    ret = main(["record", "--command", sys.executable, "-c", "print('flag_command_test')"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "flag_command_test" in captured.out
    assert "[OK] Task" in captured.out

def test_cli_record_missing_command(capsys):
    ret = main(["record", "--command"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: No command specified to record" in captured.out

def test_cli_no_options_enforcement(capsys):
    # status accepts no options
    assert main(["status", "--live"]) == 1
    captured = capsys.readouterr()
    assert "accepts no additional options" in captured.out

    # doctor accepts no options
    assert main(["doctor", "--live"]) == 1
    captured = capsys.readouterr()
    assert "accepts no additional options" in captured.out

    # cleanup accepts no options
    assert main(["cleanup", "--all"]) == 1
    captured = capsys.readouterr()
    assert "accepts no additional options" in captured.out

    # version accepts no options
    assert main(["version", "-v"]) == 1
    captured = capsys.readouterr()
    assert "accepts no additional options" in captured.out

    # show telegram accepts no options
    assert main(["show", "telegram", "--raw"]) == 1
    captured = capsys.readouterr()
    assert "accepts no additional options" in captured.out

    # show rules accepts no options
    assert main(["show", "rules", "--json"]) == 1
    captured = capsys.readouterr()
    assert "accepts no additional options" in captured.out

    # test connection accepts no options
    assert main(["test", "connection", "--all"]) == 1
    captured = capsys.readouterr()
    assert "accepts no additional options" in captured.out

def test_cli_history_options_validation(capsys):
    # Invalid limit non-integer
    assert main(["history", "--limit", "abc"]) == 1
    captured = capsys.readouterr()
    assert "positive integer" in captured.out

    # Invalid limit negative
    assert main(["history", "--limit", "-5"]) == 1
    captured = capsys.readouterr()
    assert "positive integer" in captured.out

    # Unsupported option on history
    assert main(["history", "--status", "completed"]) == 1
    captured = capsys.readouterr()
    assert "Unsupported options for history" in captured.out

def test_cli_show_user_and_reveal(capsys):
    from kerniq.config import add_user
    add_user(888999, "control")

    # show user without --reveal (masked)
    ret = main(["show", "user"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "88****99" in captured.out
    assert "888999" not in captured.out

    # show user with --reveal (unmasked)
    ret = main(["show", "user", "--reveal"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "888999" in captured.out
    assert "WARNING: Displaying unmasked Telegram User IDs" in captured.out

    # show user with invalid option
    ret = main(["show", "user", "--invalid"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Unsupported option" in captured.out

def test_cli_show_telegram(capsys):
    # When not configured
    ret = main(["show", "telegram"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Telegram Configuration & Status" in captured.out
    assert "Offline (no token configured)" in captured.out

    # When configured
    from kerniq.config import set_telegram_token
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    with patch("kerniq.telegram_client.TelegramClient.get_me", return_value={"username": "TestBot", "id": 123456789}):
        ret = main(["show", "telegram"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "@TestBot" in captured.out
        assert "Connected" in captured.out
        # Full token must NOT be in output
        assert "ABCDEF1234567890abcdef" not in captured.out
        assert "******" in captured.out

def test_cli_start_and_stop_listener(capsys):
    setup_dummy_telegram_and_user()
    with patch("kerniq.daemon.start_listener_daemon", return_value=(True, "Listener started.")):
        ret = main(["start", "listener"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Listener started" in captured.out

    with patch("kerniq.daemon.stop_listener_daemon", return_value=(True, "Listener stopped.")):
        ret = main(["stop", "listener"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Listener stopped" in captured.out

def test_cli_test_connection(capsys):
    # Without token
    ret = main(["test", "connection"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Telegram bot token is not configured" in captured.out

    # With token and user
    from kerniq.config import add_user, set_telegram_token
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    add_user(777666, "control")

    with patch("kerniq.telegram_client.TelegramClient.get_me", return_value={"username": "TestBot", "id": 123456}):
        with patch("kerniq.telegram_client.TelegramClient.send_message", return_value={"ok": True}):
            ret = main(["test", "connection"])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Test message sent successfully to authorized user 777666" in captured.out
            assert "Connection test complete" in captured.out

def test_cli_config_advance_interactive(capsys):
    # Option 1: Change device name to 'MyDevice', then Option 5 (Exit)
    inputs = iter(["1", "MyDevice", "5"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = main(["config", "advance"])
        assert ret == 0
        from kerniq.config import get_device_info
        assert get_device_info()["name"] == "MyDevice"

def test_cli_interactive_shell_builtins(capsys):
    setup_dummy_telegram_and_user()
    # Test shell with exact commands: help, pause, resume, status, history, exit
    inputs = iter([
        "help",
        "pause",
        "resume",
        "status",
        "history",
        "exit"
    ])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        with patch("kerniq.telegram_listener.TelegramListener.start_background", return_value=True):
            ret = main(["start"])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Interactive Shell Commands" in captured.out
            assert "[PAUSED]" in captured.out
            assert "[RESUMED]" in captured.out
            assert "Shell Activity:" in captured.out
            assert "Exiting Kerniq shell" in captured.out

def test_cli_interactive_shell_runs_kerniq_subcommands(capsys):
    setup_dummy_telegram_and_user()
    inputs = iter([
        "kerniq help",
        "show users",
        "exit"
    ])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        with patch("kerniq.telegram_listener.TelegramListener.start_background", return_value=True):
            ret = main(["start"])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Allowed Command Reference" in captured.out
            assert "Configured Telegram Accounts" in captured.out

def test_cli_reject_unsupported_command(capsys):
    ret = main(["unsupported_cmd"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: Unsupported command 'unsupported_cmd'" in captured.out

def test_cli_config_telegram_delete_flow(capsys):
    from kerniq.config import set_telegram_token, get_telegram_token, load_config, save_config
    # Set a token first
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    cfg = load_config()
    cfg["telegram"] = {"bot_id": 123456, "bot_username": "KerniqBot"}
    save_config(cfg)

    # Test selecting option 2 (Delete), confirming 'y'
    inputs = iter(["2", "y"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        with patch("kerniq.daemon.stop_listener_daemon", return_value=(True, "Listener stopped.")):
            ret = main(["config", "telegram"])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Current Bot: @KerniqBot" in captured.out
            assert "Delete / Remove Bot Token" in captured.out
            assert "Telegram bot token has been deleted. Bot disconnected." in captured.out
            assert get_telegram_token() is None
            assert load_config().get("telegram", {}).get("status") == "not_configured"
            assert "bot_username" not in load_config().get("telegram", {})

def test_cli_config_telegram_delete_cancelled(capsys):
    from kerniq.config import set_telegram_token, get_telegram_token, load_config, save_config
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    cfg = load_config()
    cfg["telegram"] = {"bot_id": 123456, "bot_username": "KerniqBot"}
    save_config(cfg)

    # Test selecting option 2 (Delete), declining 'n'
    inputs = iter(["2", "n"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = main(["config", "telegram"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Deletion cancelled." in captured.out
        assert get_telegram_token() is not None

def test_notifications_skipped_when_both_inactive(tmp_path, monkeypatch):
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    from kerniq.config import add_user, set_telegram_token
    from kerniq.notifications import NotificationService
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    add_user(888999, "control")

    # Neither daemon nor shell is running
    with patch("kerniq.daemon.get_listener_daemon_pid", return_value=None):
        with patch("kerniq.storage.is_shell_active", return_value=False):
            service = NotificationService(listener_active=False)
            res = service.send_notification("task_started", "Hello task", task_id="KQ-123")
            assert len(res) == 1
            assert res[0]["status"] == "skipped"
            assert res[0]["reason"] == "environment_inactive"

def test_cli_reset_cancelled(capsys):
    from kerniq.config import set_telegram_token, get_telegram_token
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")

    inputs = iter(["n"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = main(["reset"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Reset cancelled." in captured.out
        assert get_telegram_token() is not None

def test_cli_reset_confirmed(capsys):
    from kerniq.config import add_user, set_telegram_token, get_telegram_token, get_users, load_config
    from kerniq.storage import Storage

    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    add_user(111222, "control")

    # Run a command to create task record and output files
    main(["record", "--", sys.executable, "-c", "print('pre_reset_task')"])
    storage = Storage()
    assert len(storage.list_tasks()) >= 1
    assert len(get_users()) == 1

    inputs = iter(["y"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = main(["reset"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Full Destructive Reset" in captured.out
        assert "Kerniq has been completely reset to factory defaults" in captured.out

        # Verify everything is wiped
        assert get_telegram_token() is None
        assert len(get_users()) == 0
        assert len(Storage().list_tasks()) == 0
        cfg = load_config()
        assert cfg.get("telegram", {}).get("status") == "not_configured"

def test_cli_reset_force(capsys):
    from kerniq.config import add_user, set_telegram_token, get_telegram_token, get_users
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    add_user(333444, "readonly")

    ret = main(["reset", "--force"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Kerniq has been completely reset to factory defaults" in captured.out
    assert get_telegram_token() is None
    assert len(get_users()) == 0

def test_show_rules_clean_display(capsys):
    from kerniq.cli import main
    ret = main(["show", "rules"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Default-Enabled" not in captured.out
    assert "Default-Disabled" not in captured.out
    assert "Notification Rules:" in captured.out
    assert "Shell Started" in captured.out
    assert "Task Requires User Input" in captured.out
    assert "Task Running Since Long Duration" in captured.out
    assert "Task Consuming High CPU" in captured.out
    assert "Task Consuming High RAM" in captured.out
    assert "Battery Lower Than Threshold" in captured.out
    assert "Thresholds:" in captured.out
    assert "Battery Threshold" in captured.out
    assert "RAM / Memory Threshold" in captured.out
    assert "CPU Threshold" in captured.out
    assert "Duration Threshold" in captured.out
    assert "Alert Cooldown" in captured.out

def test_config_rules_flags_and_menu(capsys):
    from kerniq.cli import main
    from kerniq.config import get_rules
    from unittest.mock import patch

    # Programmatic CLI flags
    ret = main(["config", "rules", "--memory-mb", "2048.0", "--battery-percent", "15.0", "--duration-seconds", "600"])
    assert ret == 0
    rules = get_rules()
    assert rules["memory_mb"] == 2048.0
    assert rules["memory_threshold"] is True
    assert rules["battery_percent"] == 15.0
    assert rules["battery_threshold"] is True
    assert rules["duration_seconds"] == 600.0
    assert rules["duration_threshold"] is True

    # Interactive menu: Edit RAM threshold
    inputs = iter(["4", "4096"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = main(["config", "rules"])
        assert ret == 0
        assert get_rules()["memory_mb"] == 4096.0

    # Interactive menu: Disable duration rule
    inputs = iter(["2", "9"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = main(["config", "rules"])
        assert ret == 0
        assert get_rules()["duration_threshold"] is False

    # Interactive menu: Enable duration rule
    inputs = iter(["1", "9"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = main(["config", "rules"])
        assert ret == 0
        assert get_rules()["duration_threshold"] is True

def test_interactive_shell_stop_command(capsys):
    setup_dummy_telegram_and_user()
    from kerniq.cli import cmd_start
    from unittest.mock import patch

    # Interactive shell 'stop' with no tasks running
    inputs = iter(["stop", "exit"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = cmd_start()
        assert ret is None or ret == 0
        captured = capsys.readouterr()
        assert "No tasks are currently running." in captured.out

def test_cli_start_without_telegram_and_user_warns_and_succeeds(capsys):
    from kerniq.config import delete_telegram_token, set_telegram_token
    delete_telegram_token()

    # Missing both token and users: warns and enters shell
    with patch("builtins.input", return_value="exit"):
        ret = main(["start"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Warning: Telegram bot token and authorized users are not configured" in captured.out
        assert "Interactive Task Monitoring Shell" in captured.out

    # Missing users only: warns and enters shell
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    with patch("builtins.input", return_value="exit"):
        ret = main(["start"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Warning: No authorized Telegram users configured" in captured.out
        assert "Interactive Task Monitoring Shell" in captured.out

def test_cli_record_without_telegram_and_user_warns_and_succeeds(capsys):
    from kerniq.config import delete_telegram_token
    delete_telegram_token()

    ret = main(["record", "--command", sys.executable, "-c", "print('runs successfully')"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Warning: Telegram bot token and authorized users are not configured" in captured.out
    assert "runs successfully" in captured.out
    assert "[OK] Task" in captured.out

def test_cli_cannot_start_listener_without_telegram_and_user(capsys):
    from kerniq.config import delete_telegram_token
    delete_telegram_token()

    ret = main(["start", "listener"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Telegram bot token and authorized user must be configured" in captured.out

def test_cli_show_listener(capsys):
    setup_dummy_telegram_and_user()
    ret = main(["show", "listener"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Kerniq Persistent Listener Status" in captured.out
    assert "Listener Status:" in captured.out
    assert "Telegram Bot:" in captured.out
    assert "Authorized Users:" in captured.out
    assert "Daemon Log File:" in captured.out

    # Multi-word token support
    ret = main(["show listener"])
    assert ret == 0

    # Extra options rejected
    assert main(["show", "listener", "--all"]) == 1

def test_config_rules_cancel_with_n(capsys):
    # When prompted for rule to enable, typing 'n' cancels gracefully without error
    inputs = iter(["1", "n"])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        ret = main(["config", "rules"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Cancelled." in captured.out
        assert "Invalid selection." not in captured.out




