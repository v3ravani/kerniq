"""Unit tests for SQLite storage layer, migrations, task records, and cleanup."""

from pathlib import Path
import pytest

from kerniq.storage import Storage

@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("KERNIQ_HOME", str(tmp_path))
    return Storage(db_path=tmp_path / "test.db")

def test_create_and_get_task(storage):
    task = storage.create_task(
        task_id="KQ-1234",
        command="python script.py",
        args=["python", "script.py"],
        cwd="/tmp",
        pid=999,
        status="starting"
    )

    assert task["task_id"] == "KQ-1234"
    assert task["command"] == "python script.py"
    assert task["args"] == ["python", "script.py"]
    assert task["status"] == "starting"
    assert task["pid"] == 999
    assert task["exit_code"] is None
    assert Path(task["stdout_path"]).exists()
    assert Path(task["stderr_path"]).exists()

def test_update_and_finish_task(storage):
    storage.create_task(
        task_id="KQ-5678",
        command="echo hello",
        args=["echo", "hello"],
        cwd="/tmp"
    )

    storage.update_task_status("KQ-5678", status="running", pid=1024)
    running_task = storage.get_task("KQ-5678")
    assert running_task["status"] == "running"
    assert running_task["pid"] == 1024

    # Write some dummy bytes to stdout
    with open(running_task["stdout_path"], "w", encoding="utf-8") as f:
        f.write("hello world\n")

    fin = storage.finish_task(
        task_id="KQ-5678",
        status="completed",
        exit_code=0,
        duration_seconds=1.23,
        cpu_peak_percent=12.5,
        memory_peak_mb=34.2
    )

    assert fin["status"] == "completed"
    assert fin["exit_code"] == 0
    assert fin["duration_seconds"] == 1.23
    assert fin["stdout_size"] > 0
    assert fin["cpu_peak_percent"] == 12.5
    assert fin["memory_peak_mb"] == 34.2

def test_list_tasks_and_count_active(storage):
    assert storage.count_active_tasks() == 0

    storage.create_task(task_id="KQ-0001", command="cmd1", args=["cmd1"], cwd="/tmp", status="running")
    storage.create_task(task_id="KQ-0002", command="cmd2", args=["cmd2"], cwd="/tmp", status="running")
    storage.create_task(task_id="KQ-0003", command="cmd3", args=["cmd3"], cwd="/tmp", status="completed")

    assert storage.count_active_tasks() == 2

    all_tasks = storage.list_tasks(limit=10)
    assert len(all_tasks) == 3

    completed_tasks = storage.list_tasks(status="completed")
    assert len(completed_tasks) == 1
    assert completed_tasks[0]["task_id"] == "KQ-0003"

def test_cleanup_tasks_preserves_running(storage):
    t1 = storage.create_task(task_id="KQ-RUN1", command="run", args=["run"], cwd="/tmp", status="running")
    t2 = storage.create_task(task_id="KQ-DONE1", command="done", args=["done"], cwd="/tmp", status="completed")

    recs, files = storage.cleanup_old_tasks()
    assert recs == 1  # Only KQ-DONE1 deleted
    assert storage.get_task("KQ-RUN1") is not None
    assert storage.get_task("KQ-DONE1") is None
