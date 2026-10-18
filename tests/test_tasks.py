"""Integration tests for real process supervision, streaming output, exit codes, and termination."""

import sys
import time
from pathlib import Path
import pytest

from kerniq.exceptions import ProcessError
from kerniq.storage import Storage
from kerniq.tasks import TaskSupervisor, format_duration

@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    storage = Storage(db_path=tmp_path / "tasks_test.db")
    return TaskSupervisor(storage=storage)

def test_process_exit_code_zero(supervisor):
    # Run a simple python one-liner exiting 0
    cmd = [sys.executable, "-c", "import sys; print('Hello stdout'); sys.stderr.write('Hello stderr\\n'); sys.exit(0)"]
    task_id, exit_code = supervisor.run_command(cmd, tee_to_stdout=False)

    assert exit_code == 0
    task = supervisor.storage.get_task(task_id)
    assert task["status"] == "completed"
    assert task["exit_code"] == 0
    assert task["duration_seconds"] >= 0

    stdout_path = Path(task["stdout_path"])
    stderr_path = Path(task["stderr_path"])
    assert stdout_path.read_text(encoding="utf-8").strip() == "Hello stdout"
    assert stderr_path.read_text(encoding="utf-8").strip() == "Hello stderr"

def test_process_exit_code_nonzero(supervisor):
    cmd = [sys.executable, "-c", "import sys; print('Failing task'); sys.exit(42)"]
    task_id, exit_code = supervisor.run_command(cmd, tee_to_stdout=False)

    assert exit_code == 42
    task = supervisor.storage.get_task(task_id)
    assert task["status"] == "failed"
    assert task["exit_code"] == 42

def test_large_output_no_deadlock(supervisor):
    # Emit 50,000 lines of output to verify continuous stream draining without pipe deadlocks
    script = (
        "import sys\n"
        "for i in range(10000):\n"
        "    sys.stdout.write(f'line {i}\\n')\n"
        "    if i % 10 == 0:\n"
        "        sys.stderr.write(f'err {i}\\n')\n"
    )
    cmd = [sys.executable, "-c", script]
    task_id, exit_code = supervisor.run_command(cmd, tee_to_stdout=False)

    assert exit_code == 0
    task = supervisor.storage.get_task(task_id)
    assert task["status"] == "completed"
    assert task["stdout_size"] > 50000

def test_missing_executable_launch_failure(supervisor):
    cmd = ["nonexistent_binary_xyz_12345", "--arg"]
    with pytest.raises(ProcessError):
        supervisor.run_command(cmd, tee_to_stdout=False)

    # Check that a failed task record was preserved
    tasks = supervisor.storage.list_tasks(limit=1)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "failed"
    assert "Launch failure" in tasks[0].get("error_message", "")

def test_process_termination(supervisor):
    import threading

    # Long running process
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
    captured_task_id = [None]

    def _run():
        try:
            tid, _ = supervisor.run_command(cmd, tee_to_stdout=False)
            captured_task_id[0] = tid
        except Exception:
            pass

    t = threading.Thread(target=_run)
    t.start()

    # Wait until process is registered and active
    time.sleep(0.5)
    active = supervisor.storage.get_active_tasks()
    assert len(active) > 0
    target_id = active[0]["task_id"]

    # Terminate process
    success, msg = supervisor.terminate_task(target_id)
    assert success is True

    t.join(timeout=5.0)
    task = supervisor.storage.get_task(target_id)
    assert task["status"] == "terminated"

def test_duration_formatting():
    assert format_duration(0) == "00:00:00"
    assert format_duration(65) == "00:01:05"
    assert format_duration(3661) == "01:01:01"

def test_task_input_required_prompt_detection(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    storage = Storage(db_path=tmp_path / "tasks_prompt_test.db")
    mock_notifier = MagicMock()
    supervisor = TaskSupervisor(storage=storage, notifications=mock_notifier)

    script = (
        "import sys, time\n"
        "sys.stdout.write('Please enter token [y/N]: ')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.2)\n"
        "sys.exit(0)\n"
    )
    cmd = [sys.executable, "-c", script]
    task_id, exit_code = supervisor.run_command(cmd, tee_to_stdout=False)

    assert exit_code == 0
    mock_notifier.notify_task_input_required.assert_called_once()
    args, kwargs = mock_notifier.notify_task_input_required.call_args
    assert args[0] == task_id
