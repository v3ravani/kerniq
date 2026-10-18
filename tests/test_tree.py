"""Comprehensive test suite for Kerniq Process Tree Inspection and output --tree integration."""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch
import pytest

from kerniq.cli import main
from kerniq.config import add_user, set_telegram_token
from kerniq.proc_tree import get_process_display_name, render_process_tree
from kerniq.storage import Storage
from kerniq.tasks import TaskSupervisor


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    from kerniq.config import delete_telegram_token
    delete_telegram_token()
    yield tmp_path
    delete_telegram_token()


def setup_dummy_credentials():
    set_telegram_token("123456789:ABCDEF1234567890abcdef1234567890ABC")
    add_user(1001, "control")


def test_process_display_name_helpers():
    # Executables
    assert get_process_display_name("git.exe", ["git", "status"], is_root=False) == "git.exe"
    assert get_process_display_name("python.exe", ["python", "train.py"], is_root=True) == "python.exe"

    # Python scripts for child processes
    assert get_process_display_name("python.exe", ["python", "data_loader.py"], is_root=False) == "data_loader.py"
    assert get_process_display_name("python.exe", ["python", "C:\\path\\to\\worker.py", "--arg"], is_root=False) == "worker.py"
    assert get_process_display_name("python.exe", ["python", "/unix/path/to/worker.py", "--arg"], is_root=False) == "worker.py"
    assert get_process_display_name("python.exe", ["python", "-c", "print(1)"], is_root=False) == "python.exe"

    # Node scripts
    assert get_process_display_name("node.exe", ["node", "server.js"], is_root=False) == "server.js"
    assert get_process_display_name("node.exe", ["node", "C:\\path\\to\\server.js"], is_root=False) == "server.js"


def test_tree_cli_arguments_validation(capsys):
    # 1. Missing task ID
    ret = main(["tree"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: Task ID required. Usage: kerniq tree <id>" in captured.out

    # 2. Too many arguments
    ret = main(["tree", "KQ-1234", "--extra"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: 'kerniq tree' accepts only a single Task ID" in captured.out

    # 3. Unknown task ID
    ret = main(["tree", "KQ-999999"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: Task 'KQ-999999' not found." in captured.out


def test_output_cli_arguments_validation(capsys):
    setup_dummy_credentials()
    # Missing task ID
    ret = main(["output"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: Task ID required. Usage: kerniq output <id> [--tree]" in captured.out

    # Only flag without task ID
    ret = main(["output", "--tree"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: Task ID required" in captured.out

    # Unsupported flag
    ret = main(["output", "KQ-1234", "--verbose"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: Unsupported options for output" in captured.out

    # Too many arguments
    ret = main(["output", "KQ-1234", "--tree", "--extra"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: Unsupported options for output" in captured.out


def test_tree_unavailable_process_details(capsys):
    # Task exists in database, but has no PID or process tree records
    storage = Storage()
    task = storage.create_task(
        task_id="KQ-UNAVAIL",
        command="legacy_command.sh",
        args=["legacy_command.sh"],
        cwd="/tmp",
        pid=None,
        status="completed"
    )
    storage.finish_task("KQ-UNAVAIL", status="completed", exit_code=0, duration_seconds=4.5)

    ret = main(["tree", "KQ-UNAVAIL"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Kerniq | Process Tree" in captured.out
    assert "KQ-UNAVAIL" in captured.out
    assert "legacy_command.sh" in captured.out
    assert "COMPLETED (exit code: 0)" in captured.out
    assert "Root PID   N/A" in captured.out
    assert "Process tree information is unavailable for this task" in captured.out
    assert "Tracked processes: 0 (Information unavailable)" in captured.out


def test_tree_fast_completing_command(capsys):
    setup_dummy_credentials()
    # Run a fast command
    ret = main(["record", "--command", sys.executable, "-c", "print('fast_proc_output')"])
    assert ret == 0
    captured = capsys.readouterr()
    import re
    match = re.search(r"KQ-[0-9A-Fa-f]+", captured.out)
    assert match is not None
    task_id = match.group(0)

    # Inspect tree
    ret = main(["tree", task_id])
    assert ret == 0
    captured_tree = capsys.readouterr()
    assert "Kerniq | Process Tree" in captured_tree.out
    assert task_id in captured_tree.out
    assert "COMPLETED" in captured_tree.out
    assert "PROCESS HIERARCHY" in captured_tree.out
    assert "SUMMARY" in captured_tree.out
    assert "Tracked processes: 1" in captured_tree.out


def test_tree_failing_command_with_nonzero_exit_code(capsys):
    setup_dummy_credentials()
    ret = main(["record", "--command", sys.executable, "-c", "import sys; print('fail_proc'); sys.exit(42)"])
    assert ret == 42
    captured = capsys.readouterr()
    import re
    match = re.search(r"KQ-[0-9A-Fa-f]+", captured.out)
    assert match is not None
    task_id = match.group(0)

    # Inspect tree
    ret = main(["tree", task_id])
    assert ret == 0
    captured_tree = capsys.readouterr()
    assert "Kerniq | Process Tree" in captured_tree.out
    assert task_id in captured_tree.out
    assert "FAILED (exit code: 42)" in captured_tree.out
    assert "exit: 42" in captured_tree.out
    assert "Tracked processes: 1" in captured_tree.out


def test_tree_multiple_child_processes(capsys):
    setup_dummy_credentials()
    # Script that launches multiple child processes
    parent_script = (
        "import subprocess, sys, time\n"
        "p1 = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.3)'])\n"
        "p2 = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.3)'])\n"
        "p1.wait()\n"
        "p2.wait()\n"
    )
    ret = main(["record", "--command", sys.executable, "-c", parent_script])
    assert ret == 0
    captured = capsys.readouterr()
    import re
    match = re.search(r"KQ-[0-9A-Fa-f]+", captured.out)
    assert match is not None
    task_id = match.group(0)

    # Inspect tree
    ret = main(["tree", task_id])
    assert ret == 0
    tree_out = capsys.readouterr().out
    assert "Kerniq | Process Tree" in tree_out
    assert task_id in tree_out
    assert "PROCESS HIERARCHY" in tree_out
    # Check tree branch characters
    assert ("├──" in tree_out or "└──" in tree_out)
    # Check that children were tracked
    assert "Tracked processes: 3" in tree_out


def test_tree_command_inspected_while_running(capsys):
    setup_dummy_credentials()
    import threading

    storage = Storage()
    from kerniq.notifications import NotificationService
    supervisor = TaskSupervisor(storage=storage, notifications=NotificationService())

    # Long running command
    script = "import time; time.sleep(2.0)"
    captured_tid = [None]

    def _worker():
        tid, code = supervisor.run_command([sys.executable, "-c", script], tee_to_stdout=False)
        captured_tid[0] = tid

    t = threading.Thread(target=_worker)
    t.start()

    # Wait until process is active and running
    time.sleep(0.4)
    active = storage.get_active_tasks()
    assert len(active) > 0
    tid = active[0]["task_id"]

    # Inspect tree while running
    ret = main(["tree", tid])
    assert ret == 0
    running_tree = capsys.readouterr().out
    assert "Kerniq | Process Tree" in running_tree
    assert tid in running_tree
    assert "RUNNING" in running_tree
    assert "PROCESS HIERARCHY" in running_tree
    assert "Tracked processes:" in running_tree

    t.join(timeout=4.0)


def test_tree_child_exits_before_task_finishes(capsys):
    setup_dummy_credentials()
    # Parent launches one child that exits quickly, and another that runs longer
    script = (
        "import subprocess, sys, time\n"
        "p_short = subprocess.Popen([sys.executable, '-c', 'import time, sys; time.sleep(0.15); sys.exit(0)'])\n"
        "p_short.wait()\n"
        "time.sleep(0.2)\n"
        "p_long = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.25)'])\n"
        "p_long.wait()\n"
    )
    ret = main(["record", "--command", sys.executable, "-c", script])
    assert ret == 0
    captured = capsys.readouterr()
    import re
    match = re.search(r"KQ-[0-9A-Fa-f]+", captured.out)
    assert match is not None
    task_id = match.group(0)

    ret = main(["tree", task_id])
    assert ret == 0
    tree_out = capsys.readouterr().out
    assert "Kerniq | Process Tree" in tree_out
    assert task_id in tree_out
    # All children including short-lived child captured
    assert "Tracked processes: 3" in tree_out
    assert "TERMINATED" in tree_out


def test_output_and_output_tree_flag(capsys):
    setup_dummy_credentials()
    # Run a task
    main(["record", "--command", sys.executable, "-c", "print('STANDARD_OUTPUT_TEST_LINE')"])
    captured = capsys.readouterr()
    import re
    match = re.search(r"KQ-[0-9A-Fa-f]+", captured.out)
    assert match is not None
    task_id = match.group(0)

    # 1. Plain output command: contains output but not process tree
    ret = main(["output", task_id])
    assert ret == 0
    plain_out = capsys.readouterr().out
    assert "STANDARD_OUTPUT_TEST_LINE" in plain_out
    assert "--- Standard Output (stdout) ---" in plain_out
    assert "Kerniq | Process Tree" not in plain_out
    assert "PROCESS HIERARCHY" not in plain_out

    # 2. Output with --tree flag: contains BOTH output and process tree
    ret = main(["output", task_id, "--tree"])
    assert ret == 0
    tree_out = capsys.readouterr().out
    assert "STANDARD_OUTPUT_TEST_LINE" in tree_out
    assert "--- Standard Output (stdout) ---" in tree_out
    assert "Kerniq | Process Tree" in tree_out
    assert "PROCESS HIERARCHY" in tree_out
    assert "SUMMARY" in tree_out
    assert "Tracked processes: 1" in tree_out

    # 3. Reversed flag position: output --tree <id>
    ret = main(["output", "--tree", task_id])
    assert ret == 0
    reversed_out = capsys.readouterr().out
    assert "STANDARD_OUTPUT_TEST_LINE" in reversed_out
    assert "Kerniq | Process Tree" in reversed_out


def test_interactive_shell_tree_and_output_commands(capsys):
    setup_dummy_credentials()
    # Create a task first
    main(["record", "--command", sys.executable, "-c", "print('shell_task_marker')"])
    captured = capsys.readouterr()
    import re
    match = re.search(r"KQ-[0-9A-Fa-f]+", captured.out)
    assert match is not None
    task_id = match.group(0)

    # Inside interactive shell: run tree <id>, output <id> --tree, then exit
    inputs = iter([
        f"tree {task_id}",
        f"output {task_id} --tree",
        "exit"
    ])
    with patch("builtins.input", lambda prompt="": next(inputs)):
        with patch("kerniq.telegram_listener.TelegramListener.start_background", return_value=True):
            ret = main(["start"])
            assert ret == 0
            shell_captured = capsys.readouterr().out
            assert "Kerniq | Process Tree" in shell_captured
            assert "shell_task_marker" in shell_captured
            assert "Exiting Kerniq shell" in shell_captured


def test_no_color_respected_in_tree(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    task = {
        "task_id": "KQ-COLOR1",
        "command": "python test.py",
        "status": "completed",
        "exit_code": 0,
        "pid": 1234,
        "duration_seconds": 2.5
    }
    tree_data = {
        "task_id": "KQ-COLOR1",
        "root_pid": 1234,
        "nodes": {
            "1234": {
                "pid": 1234,
                "ppid": None,
                "name": "python.exe",
                "display_name": "python.exe",
                "status": "TERMINATED",
                "exit_code": 0,
                "duration_seconds": 2.5,
                "children": []
            }
        }
    }
    rendered = render_process_tree(task, tree_data)
    # When NO_COLOR is set, ANSI escape code \033[ should not be present
    assert "\033[" not in rendered
    assert "Kerniq | Process Tree" in rendered
    assert "Tracked processes: 1" in rendered
