"""Tests for refined Kerniq history and detailed history output."""

import re
import sys
from unittest.mock import patch
import pytest

from kerniq.cli import main
from kerniq.storage import Storage

@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    from kerniq.config import delete_telegram_token
    delete_telegram_token()
    yield tmp_path
    delete_telegram_token()


def test_history_empty(capsys):
    ret = main(["history"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Saved Task History" in captured.out
    assert "0 tasks found" in captured.out


def test_history_standard_layout(capsys):
    storage = Storage()
    storage.create_task("KQ-1042", "python train.py", ["train.py"], "C:/repo", pid=100)
    storage.finish_task(
        task_id="KQ-1042",
        status="completed",
        exit_code=0,
        duration_seconds=382.0,
        cpu_peak_percent=81.6,
        memory_peak_mb=512.0
    )

    storage.create_task("KQ-1041", "npm run build", ["run", "build"], "C:/repo", pid=101)
    storage.finish_task(
        task_id="KQ-1041",
        status="failed",
        exit_code=1,
        duration_seconds=11.0,
        error_message="Build process exited with a non-zero status."
    )

    ret = main(["history"])
    assert ret == 0
    out = capsys.readouterr().out

    # Header and divider
    assert "Saved Task History" in out
    assert "─" in out

    # Headings: KQ-XXXX | command
    assert "KQ-1042 | python train.py" in out
    assert "KQ-1041 | npm run build" in out

    # Start time present in standard history
    assert "Start Time:" in out
    assert "2 tasks found" in out

    # Standard history must NOT display detailed fields
    assert "Status:" not in out
    assert "Exit Code:" not in out
    assert "Duration:" not in out
    assert "Resource Utilization:" not in out
    assert "Error:" not in out


def test_history_detailed_layout(capsys):
    storage = Storage()
    storage.create_task("KQ-1042", "python train.py", ["train.py"], "C:/repo", pid=100)
    storage.save_task_process_tree("KQ-1042", {
        "task_id": "KQ-1042",
        "root_pid": 100,
        "nodes": {
            "100": {"pid": 100, "name": "python.exe"},
            "101": {"pid": 101, "name": "worker1.exe"},
            "102": {"pid": 102, "name": "worker2.exe"},
            "103": {"pid": 103, "name": "worker3.exe"},
        }
    })
    storage.finish_task(
        task_id="KQ-1042",
        status="completed",
        exit_code=0,
        duration_seconds=382.0,
        cpu_peak_percent=81.6,
        memory_peak_mb=512.0,
        cpu_avg_percent=34.2
    )

    storage.create_task("KQ-1041", "npm run build", ["run", "build"], "C:/repo", pid=200)
    storage.finish_task(
        task_id="KQ-1041",
        status="failed",
        exit_code=1,
        duration_seconds=11.0,
        error_message="Build process exited with a non-zero status."
    )

    ret = main(["history", "--detailed"])
    assert ret == 0
    out = capsys.readouterr().out

    assert "Saved Task History (Detailed)" in out
    assert "─" in out

    # Check Task 1 (Completed with metrics and process tree)
    assert "KQ-1042 | python train.py" in out
    assert "Status: COMPLETED" in out
    assert "Start Time:" in out
    assert "End Time:" in out
    assert "Duration: 00:06:22" in out
    assert "Exit Code: 0" in out
    assert "Resource Utilization:" in out
    assert "Average CPU: 34.2%" in out
    assert "Peak CPU: 81.6%" in out
    assert "Peak Memory: 512 MB" in out
    assert "Tracked Processes: 4" in out

    # Check Task 2 (Failed with error message)
    assert "KQ-1041 | npm run build" in out
    assert "Status: FAILED" in out
    assert "Exit Code: 1" in out
    assert "Duration: 00:00:11" in out
    assert "Error: Build process exited with a non-zero status." in out

    assert "2 tasks found" in out


def test_history_detailed_with_limit_both_orders(capsys):
    storage = Storage()
    for i in range(1, 4):
        storage.create_task(f"KQ-100{i}", f"cmd_{i}", [f"cmd_{i}"], "C:/repo")
        storage.finish_task(f"KQ-100{i}", "completed", 0, duration_seconds=float(i * 10))

    # Test --detailed --limit 2
    ret1 = main(["history", "--detailed", "--limit", "2"])
    assert ret1 == 0
    out1 = capsys.readouterr().out
    assert "2 tasks found" in out1
    assert "KQ-1003" in out1
    assert "KQ-1002" in out1
    assert "KQ-1001" not in out1

    # Test --limit 1 --detailed
    ret2 = main(["history", "--limit", "1", "--detailed"])
    assert ret2 == 0
    out2 = capsys.readouterr().out
    assert "1 task found" in out2
    assert "KQ-1003" in out2
    assert "KQ-1002" not in out2


def test_history_running_task_omits_finished_fields(capsys):
    storage = Storage()
    storage.create_task("KQ-9999", "long_job.py", ["long_job.py"], "C:/repo", pid=999, status="running")

    ret = main(["history", "--detailed"])
    assert ret == 0
    out = capsys.readouterr().out

    assert "KQ-9999 | long_job.py" in out
    assert "Status: RUNNING" in out
    assert "Start Time:" in out
    # Running tasks do not have End Time or Exit Code
    assert "End Time:" not in out
    assert "Exit Code:" not in out
    assert "(running)" in out


def test_history_missing_metrics_not_fabricated(capsys):
    storage = Storage()
    storage.create_task("KQ-7777", "quick.py", ["quick.py"], "C:/repo", pid=777)
    storage.finish_task(
        task_id="KQ-7777",
        status="completed",
        exit_code=0,
        duration_seconds=0.05,
        cpu_peak_percent=None,
        memory_peak_mb=None,
        cpu_avg_percent=None
    )

    ret = main(["history", "--detailed"])
    assert ret == 0
    out = capsys.readouterr().out

    assert "KQ-7777 | quick.py" in out
    assert "Status: COMPLETED" in out
    # No fabricated metrics should be displayed
    assert "Resource Utilization:" not in out
    assert "Average CPU:" not in out
    assert "Peak CPU:" not in out
    assert "Peak Memory:" not in out
    assert "0.0%" not in out


def test_history_respects_no_color(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    storage = Storage()
    storage.create_task("KQ-5555", "app.py", ["app.py"], "C:/repo")
    storage.finish_task("KQ-5555", "completed", 0, duration_seconds=5.0)

    ret = main(["history", "--detailed"])
    assert ret == 0
    out = capsys.readouterr().out

    assert "KQ-5555 | app.py" in out
    assert "Status: COMPLETED" in out
    # Verify NO ANSI escape codes are in output
    assert "\033[" not in out


def test_history_interactive_shell_execution(capsys):
    from kerniq.config import set_telegram_token, add_user
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    add_user(1001, "control")

    storage = Storage()
    storage.create_task("KQ-4444", "shell_job.py", ["shell_job.py"], "C:/repo")
    storage.finish_task("KQ-4444", "completed", 0, duration_seconds=12.0)

    inputs = iter([
        "history",
        "history --detailed",
        "exit"
    ])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        with patch("kerniq.telegram_listener.TelegramListener.start_background", return_value=True):
            ret = main(["start"])
            assert ret == 0
            captured = capsys.readouterr()
            assert "Saved Task History" in captured.out
            assert "KQ-4444 | shell_job.py" in captured.out
            assert "Saved Task History (Detailed)" in captured.out
            assert "Status: COMPLETED" in captured.out
