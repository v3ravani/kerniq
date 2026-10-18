"""Unit and integration tests for new CLI commands, encryption, numeric IDs, and Telegram capabilities."""
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from kerniq.cli import main, cmd_task_action, cmd_doctor
from kerniq.tasks import generate_task_id, TaskSupervisor
from kerniq.storage import Storage
from kerniq.config import (
    get_config_path,
    get_credentials_path,
    load_config,
    save_config,
    set_telegram_token,
    get_telegram_token,
    add_user,
    get_users,
    encrypt_str,
    decrypt_str,
)
from kerniq.proc_tree import format_process_events, format_resource_consumption


def test_numeric_kq_task_ids():
    """Verify that generated task IDs strictly follow KQ-XXXX format where XXXX is 4 digits."""
    for _ in range(50):
        tid = generate_task_id()
        assert tid.startswith("KQ-")
        number_part = tid.split("-", 1)[1]
        assert len(number_part) == 4
        assert number_part.isdigit()
        val = int(number_part)
        assert 1000 <= val <= 9999


def test_fernet_credential_and_user_encryption(tmp_path, monkeypatch):
    """Verify that Telegram bot token and user IDs are never stored unencrypted on disk."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    token = "987654321:AAFakeBotTokenForEncryptionTesting123"
    set_telegram_token(token)

    # Check credentials.json content directly
    cred_file = get_credentials_path()
    assert cred_file.exists()
    raw_cred = cred_file.read_text(encoding="utf-8")
    assert token not in raw_cred
    data = json.loads(raw_cred)
    assert "telegram_bot_token_enc" in data
    assert "telegram_bot_token" not in data

    # Decrypt via API
    retrieved = get_telegram_token()
    assert retrieved == token

    # Check user ID encryption in config.json
    uid = 99887766
    add_user(uid, "control", nickname="TesterNick")

    cfg_file = get_config_path()
    assert cfg_file.exists()
    raw_cfg = cfg_file.read_text(encoding="utf-8")
    assert str(uid) not in raw_cfg
    cfg_data = json.loads(raw_cfg)
    users_raw = cfg_data.get("users", [])
    assert len(users_raw) == 1
    assert "user_id_enc" in users_raw[0]
    assert "user_id" not in users_raw[0]
    assert users_raw[0].get("nickname") == "TesterNick"

    # Transparent decryption on load
    users = get_users()
    assert len(users) == 1
    assert users[0]["user_id"] == uid
    assert users[0]["nickname"] == "TesterNick"


def test_cli_bare_developer_attribution(capsys):
    """Verify that bare 'kerniq' command displays Viraj Ravani attribution."""
    ret = main([])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Developed by: Viraj Ravani" in captured.out
    assert "Kerniq version" in captured.out
    assert "Directory:" in captured.out


def test_cli_help_telegram(capsys):
    """Verify that 'kerniq help -telegram' shows Telegram commands help."""
    ret = main(["help", "-telegram"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Telegram Bot Commands Reference" in captured.out
    assert "/about" in captured.out
    assert "/tree <id>" in captured.out
    assert "/events <id>" in captured.out
    assert "/consumption <id>" in captured.out


def test_cli_help_shell(capsys):
    """Verify that 'kerniq help --shell' shows Interactive Shell commands help."""
    ret = main(["help", "--shell"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Interactive Shell Commands Reference" in captured.out
    assert "pause" in captured.out
    assert "resume" in captured.out
    assert "status" in captured.out
    assert "stop [<id>]" in captured.out


def test_cli_doctor_test_mode(capsys):
    """Verify that 'kerniq doctor -test' runs test suite and reports score."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="collected 110 items\n110 passed in 10.0s\n"
        )
        ret = main(["doctor", "-test"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Live Test Suite (-test)" in captured.out
        assert "Test Score: 100" in captured.out


def test_cli_cleanup_keep_count(capsys):
    """Verify that 'kerniq cleanup -k 3' cleans tasks while keeping last N."""
    storage = Storage()
    # Create dummy tasks
    for i in range(5):
        storage.create_task(f"KQ-200{i}", f"echo {i}")
        storage.update_task_status(f"KQ-200{i}", "completed", exit_code=0)

    ret = main(["cleanup", "-k", "2"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Cleanup completed" in captured.out
    assert "kept last 2" in captured.out


def test_cli_task_action_merged_options(capsys):
    """Verify that 'kerniq <id>' handles merged options (-output, -events, -consumption)."""
    storage = Storage()
    tid = "KQ-3001"
    storage.create_task(tid, "python train.py", pid=1234)
    storage.update_task_status(tid, "completed", exit_code=0)

    # Test single task overview when no options given
    ret = main([tid])
    assert ret == 0
    captured = capsys.readouterr()
    assert f"Task: {tid}" in captured.out
    assert "python train.py" in captured.out

    # Test merged options
    ret = main([tid, "-events", "-consumption"])
    assert ret == 0
    captured = capsys.readouterr()
    assert f"PROCESS EVENTS - {tid}" in captured.out
    assert f"RESOURCE CONSUMPTION - {tid}" in captured.out


def test_cli_task_action_remove(capsys):
    """Verify that 'kerniq <id> -remove' deletes the task from storage."""
    storage = Storage()
    tid = "KQ-3002"
    storage.create_task(tid, "python build.py")
    assert storage.get_task(tid) is not None

    ret = main([tid, "-remove"])
    assert ret == 0
    captured = capsys.readouterr()
    assert f"Task {tid} removed from history." in captured.out
    assert storage.get_task(tid) is None


def test_cli_force_reset(capsys):
    """Verify that 'kerniq force reset' resets without interactive confirmation."""
    ret = main(["force", "reset"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "reset to factory defaults" in captured.out


def test_telegram_about_command():
    """Verify /about Telegram command displays Viraj Ravani attribution."""
    from kerniq.telegram_listener import TelegramListener
    storage = Storage()
    supervisor = TaskSupervisor(storage)
    listener = TelegramListener(supervisor=supervisor, storage=storage)
    mock_client = MagicMock()

    add_user(5001, "readonly")
    listener._handle_message({"from": {"id": 5001}, "chat": {"id": 123}, "text": "/about"}, mock_client)
    reply = mock_client.send_message.call_args[1]["text"]
    assert "Viraj Ravani" in reply
    assert "Kerniq" in reply


def test_telegram_tree_events_consumption_commands():
    """Verify /tree, /events, /consumption Telegram commands respond with expected information."""
    from kerniq.telegram_listener import TelegramListener
    storage = Storage()
    supervisor = TaskSupervisor(storage)
    listener = TelegramListener(supervisor=supervisor, storage=storage)
    mock_client = MagicMock()

    tid = "KQ-4001"
    storage.create_task(tid, "python script.py", pid=5678)
    storage.update_task_status(tid, "completed", exit_code=0)

    add_user(5002, "control")

    # /tree
    listener._handle_message({"from": {"id": 5002}, "chat": {"id": 123}, "text": f"/tree {tid}"}, mock_client)
    tree_reply = mock_client.send_message.call_args[1]["text"]
    assert "Process Tree" in tree_reply and tid in tree_reply

    # /events
    listener._handle_message({"from": {"id": 5002}, "chat": {"id": 123}, "text": f"/events {tid}"}, mock_client)
    events_reply = mock_client.send_message.call_args[1]["text"]
    assert f"PROCESS EVENTS - {tid}" in events_reply

    # /consumption
    listener._handle_message({"from": {"id": 5002}, "chat": {"id": 123}, "text": f"/consumption {tid}"}, mock_client)
    consumption_reply = mock_client.send_message.call_args[1]["text"]
    assert f"RESOURCE CONSUMPTION - {tid}" in consumption_reply


def test_cli_record_double_dash(capsys):
    """Verify 'kerniq record -- <command>' runs external command under supervision."""
    import sys
    ret = main(["record", "--", sys.executable, "-c", "print('hello_from_record')"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "hello_from_record" in captured.out
    assert "[OK] Task" in captured.out


def test_cli_status_subsystem_options(capsys):
    """Verify 'kerniq status' works with --all, --listener, --telegram, --rules, --users."""
    # --all
    assert main(["status", "--all"]) == 0
    out_all = capsys.readouterr().out
    assert "Kerniq System Status" in out_all

    # --listener
    assert main(["status", "--listener"]) == 0
    out_l = capsys.readouterr().out
    assert "Persistent Listener Status" in out_l

    # --telegram
    assert main(["status", "--telegram"]) == 0
    out_tg = capsys.readouterr().out
    assert "Telegram Configuration" in out_tg

    # --rules
    assert main(["status", "--rules"]) == 0
    out_r = capsys.readouterr().out
    assert "Notification Rules" in out_r

    # --users
    assert main(["status", "--users"]) == 0
    out_u = capsys.readouterr().out
    assert "Configured Telegram Accounts" in out_u


def test_cli_shell_prevents_nesting_and_runs_commands(capsys):
    """Verify interactive shell prevents nesting ('shell' or 'kerniq shell') and dispatches commands."""
    inputs = iter([
        "shell",
        "kerniq shell",
        "about",
        "logs",
        "status --listener",
        "exit",
    ])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        with patch("kerniq.telegram_listener.TelegramListener.start_background", return_value=False):
            ret = main(["shell"])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Already inside the interactive Kerniq shell." in captured.out
            assert "Viraj Ravani" in captured.out
            assert "Kerniq System Activity Logs" in captured.out
            assert "Persistent Listener Status" in captured.out
            assert "Exiting Kerniq shell." in captured.out


def test_cli_task_action_export_and_options(capsys, tmp_path):
    """Verify 'kerniq <id> --export' exports task report and options work with double dashes."""
    storage = Storage()
    tid = "KQ-5001"
    storage.create_task(tid, "python train_model.py")
    storage.update_task_status(tid, "completed", exit_code=0)

    # Test --tree, --output, --export
    ret = main([tid, "--output", "--export"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Saved Output for Task: KQ-5001" in captured.out
    assert "Task report exported successfully" in captured.out


def test_cli_config_menu_and_flags(capsys):
    """Verify 'kerniq config' opens interactive menu and accepts flags."""
    # Bare config with cancel (0)
    with patch("builtins.input", return_value="0"):
        ret = main(["config"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Kerniq Configuration Menu" in captured.out
        assert "Telegram Bot Token & Credentials" in captured.out

    # Config flags
    with patch("kerniq.cli.cmd_config_telegram", return_value=0):
        assert main(["config", "--telegram"]) == 0
    with patch("kerniq.cli.cmd_config_rules", return_value=0):
        assert main(["config", "--rules"]) == 0
    with patch("kerniq.cli.cmd_config_users", return_value=0):
        assert main(["config", "--users"]) == 0
    with patch("kerniq.cli.cmd_config_advance", return_value=0):
        assert main(["config", "--advance"]) == 0


def test_cli_ping_telegram(capsys):
    """Verify 'kerniq ping telegram' options (--id, --all) and failure when not configured."""
    # When no token
    ret = main(["ping", "telegram"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Telegram bot token is not configured" in captured.out

    # Configured
    set_telegram_token("123456789:AAFakeTokenForPingTestingABCDE")
    add_user(12345678, "control", nickname="PingUser")

    with patch("kerniq.telegram_client.TelegramClient.get_me", return_value={"username": "BotPing", "id": 111}):
        with patch("kerniq.telegram_client.TelegramClient.send_message", return_value={"ok": True}):
            # Ping with --id
            ret = main(["ping", "telegram", "--id", "12345678"])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Ping complete" in captured.out

            # Ping with --all
            ret = main(["ping", "telegram", "--all"])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Ping complete" in captured.out


def test_cli_history_export(capsys):
    """Verify 'kerniq history --limit 5 --export' displays and exports history."""
    storage = Storage()
    storage.create_task("KQ-6001", "npm run build")
    storage.update_task_status("KQ-6001", "completed", exit_code=0)

    ret = main(["history", "--limit", "5", "--export"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Saved Task History" in captured.out
    assert "History exported successfully" in captured.out


def test_cli_about(capsys):
    """Verify 'kerniq about' displays full project and author attribution."""
    ret = main(["about"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Viraj Ravani" in captured.out
    assert "https://github.com/v3ravani/kerniq" in captured.out


def test_cli_logs_and_export(capsys):
    """Verify 'kerniq logs' and 'kerniq logs --export' display internal activity logs."""
    storage = Storage()
    storage.add_app_log("test_action", "details for test action")

    ret = main(["logs"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Kerniq System Activity Logs" in captured.out
    assert "test_action" in captured.out

    ret = main(["logs", "--export"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Activity logs exported successfully" in captured.out


def test_cli_find(capsys):
    """Verify 'kerniq find <query>' searches tasks by ID, output words, exit code, or state."""
    storage = Storage()
    tid = "KQ-7001"
    storage.create_task(tid, "cargo build --release")
    storage.update_task_status(tid, "failed", exit_code=101)

    # Search by ID
    ret = main(["find", "KQ-7001"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "KQ-7001" in captured.out

    # Search by state/word
    ret = main(["find", "failed"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "KQ-7001" in captured.out


def test_telegram_running_and_report_commands():
    """Verify /running has inline buttons and /report <id> displays report with download button."""
    from kerniq.telegram_listener import TelegramListener
    storage = Storage()
    supervisor = TaskSupervisor(storage)
    listener = TelegramListener(supervisor=supervisor, storage=storage)
    mock_client = MagicMock()

    tid = "KQ-8001"
    storage.create_task(tid, "python long_job.py", pid=9999)
    # Task is running
    # User 5002 already exists with control permission from previous test

    # /running
    listener._handle_message({"from": {"id": 5002}, "chat": {"id": 123}, "text": "/running"}, mock_client)
    running_args = mock_client.send_message.call_args[1]
    assert "Monitored Running Tasks" in running_args["text"]
    assert "reply_markup" in running_args
    # Check that inline stop and output buttons are present
    buttons = running_args["reply_markup"]["inline_keyboard"]
    button_texts = [btn["text"] for row in buttons for btn in row]
    assert any("Stop" in b for b in button_texts)
    assert any("Output" in b for b in button_texts)

    # /report <id>
    listener._handle_message({"from": {"id": 5002}, "chat": {"id": 123}, "text": f"/report {tid}"}, mock_client)
    report_args = mock_client.send_message.call_args[1]
    assert f"Task Report - {tid}" in report_args["text"]
    report_buttons = [btn["text"] for row in report_args["reply_markup"]["inline_keyboard"] for btn in row]
    assert any("Download Report" in b for b in report_buttons)

    # Callback query to download report
    query = {
        "id": "q123",
        "from": {"id": 5002},
        "message": {"chat": {"id": 123}},
        "data": f"report_dl:{tid}"
    }
    listener._handle_callback_query(query, mock_client)
    mock_client.send_document.assert_called()
    assert mock_client.send_document.call_args[1]["chat_id"] == 123
