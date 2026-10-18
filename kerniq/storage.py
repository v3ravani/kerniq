"""SQLite persistence layer and local output storage for Kerniq."""

import json
import os
import shlex
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kerniq.config import get_db_path, get_output_dir
from kerniq.exceptions import StorageError

SCHEMA_VERSION = 1

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class Storage:
    """Manages SQLite database connections, schema migrations, and queries."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_db_path()
        self.init_db()

    @contextmanager
    def connection(self):
        """Context manager for SQLite connection with foreign keys and WAL mode."""
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise StorageError(f"Database error: {e}")
        finally:
            conn.close()

    def init_db(self) -> None:
        """Initialize tables, indexes, and apply migrations if necessary."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );
            """)

            cur = conn.execute("SELECT version FROM schema_version LIMIT 1;")
            row = cur.fetchone()
            current_version = row["version"] if row else 0

            if current_version < 1:
                # Schema version 1
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        task_id TEXT PRIMARY KEY,
                        command TEXT NOT NULL,
                        args_json TEXT NOT NULL,
                        cwd TEXT NOT NULL,
                        status TEXT NOT NULL,
                        pid INTEGER,
                        exit_code INTEGER,
                        start_time TEXT NOT NULL,
                        end_time TEXT,
                        duration_seconds REAL,
                        stdout_path TEXT,
                        stderr_path TEXT,
                        stdout_size INTEGER DEFAULT 0,
                        stderr_size INTEGER DEFAULT 0,
                        output_truncated INTEGER DEFAULT 0,
                        error_message TEXT,
                        cpu_peak_percent REAL,
                        memory_peak_mb REAL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_start_time ON tasks(start_time DESC);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS task_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        details_json TEXT,
                        FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
                """)

                conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (1);")

            if current_version < 2:
                # Schema version 2: add process_tree_json column to tasks
                cur = conn.execute("PRAGMA table_info(tasks);")
                columns = [r["name"] for r in cur.fetchall()]
                if "process_tree_json" not in columns:
                    conn.execute("ALTER TABLE tasks ADD COLUMN process_tree_json TEXT;")
                conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (2);")

            if current_version < 3:
                # Schema version 3: add cpu_avg_percent column to tasks
                cur = conn.execute("PRAGMA table_info(tasks);")
                columns = [r["name"] for r in cur.fetchall()]
                if "cpu_avg_percent" not in columns:
                    conn.execute("ALTER TABLE tasks ADD COLUMN cpu_avg_percent REAL;")
                conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (3);")

            if current_version < 4:
                # Schema version 4: add app_logs table for system activity tracking
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        action TEXT NOT NULL,
                        details TEXT
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_app_logs_timestamp ON app_logs(timestamp DESC);
                """)
                conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (4);")

    # -----------------------------------------------------------------------
    # Task Operations
    # -----------------------------------------------------------------------

    def create_task(
        self,
        task_id: str,
        command: str,
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        pid: Optional[int] = None,
        status: str = "starting"
    ) -> Dict[str, Any]:
        """Create a new task record."""
        now = _utcnow_iso()
        actual_args = args if args is not None else shlex.split(command)
        actual_cwd = cwd if cwd is not None else os.getcwd()
        args_json = json.dumps(actual_args)

        # Output file paths
        task_out_dir = get_output_dir() / task_id
        task_out_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = str(task_out_dir / "stdout.log")
        stderr_path = str(task_out_dir / "stderr.log")

        # Initialize empty output files
        Path(stdout_path).touch(exist_ok=True)
        Path(stderr_path).touch(exist_ok=True)

        with self.connection() as conn:
            conn.execute("""
                INSERT INTO tasks (
                    task_id, command, args_json, cwd, status, pid,
                    start_time, stdout_path, stderr_path,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                task_id, command, args_json, actual_cwd, status, pid,
                now, stdout_path, stderr_path, now, now
            ))

        self.record_event(task_id, "task_created", {"status": status, "pid": pid})
        return self.get_task(task_id)

    def update_task_status(
        self,
        task_id: str,
        status: str,
        pid: Optional[int] = None,
        error_message: Optional[str] = None,
        exit_code: Optional[int] = None
    ) -> None:
        """Update status and optional pid/error/exit_code."""
        now = _utcnow_iso()
        with self.connection() as conn:
            conn.execute("""
                UPDATE tasks SET
                    status = ?,
                    pid = coalesce(?, pid),
                    error_message = coalesce(?, error_message),
                    exit_code = coalesce(?, exit_code),
                    updated_at = ?
                WHERE task_id = ?;
            """, (status, pid, error_message, exit_code, now, task_id))

        self.record_event(task_id, f"status_{status}", {"error_message": error_message, "exit_code": exit_code})

    def finish_task(
        self,
        task_id: str,
        status: str,
        exit_code: Optional[int],
        duration_seconds: float,
        error_message: Optional[str] = None,
        cpu_peak_percent: Optional[float] = None,
        memory_peak_mb: Optional[float] = None,
        process_tree_json: Optional[str] = None,
        cpu_avg_percent: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Finalize task execution with exit code, duration, output sizes, peak metrics, and process tree."""
        now = _utcnow_iso()
        task = self.get_task(task_id)
        if not task:
            raise StorageError(f"Cannot finish task {task_id}: record does not exist.")

        # Compute output sizes
        stdout_size = 0
        stderr_size = 0
        if task.get("stdout_path") and Path(task["stdout_path"]).exists():
            stdout_size = Path(task["stdout_path"]).stat().st_size
        if task.get("stderr_path") and Path(task["stderr_path"]).exists():
            stderr_size = Path(task["stderr_path"]).stat().st_size

        with self.connection() as conn:
            conn.execute("""
                UPDATE tasks SET
                    status = ?,
                    exit_code = ?,
                    end_time = ?,
                    duration_seconds = ?,
                    stdout_size = ?,
                    stderr_size = ?,
                    error_message = coalesce(?, error_message),
                    cpu_peak_percent = coalesce(?, cpu_peak_percent),
                    memory_peak_mb = coalesce(?, memory_peak_mb),
                    process_tree_json = coalesce(?, process_tree_json),
                    cpu_avg_percent = coalesce(?, cpu_avg_percent),
                    updated_at = ?
                WHERE task_id = ?;
            """, (
                status, exit_code, now, duration_seconds,
                stdout_size, stderr_size, error_message,
                cpu_peak_percent, memory_peak_mb, process_tree_json,
                cpu_avg_percent, now, task_id
            ))

        self.record_event(task_id, "task_finished", {
            "status": status,
            "exit_code": exit_code,
            "duration_seconds": duration_seconds
        })
        return self.get_task(task_id)

    def save_task_process_tree(self, task_id: str, tree_data: Dict[str, Any]) -> None:
        """Persist process tree data to output directory and database."""
        raw_json = json.dumps(tree_data, indent=2)
        task_out_dir = get_output_dir() / task_id
        task_out_dir.mkdir(parents=True, exist_ok=True)
        tree_file = task_out_dir / "process_tree.json"
        try:
            tree_file.write_text(raw_json, encoding="utf-8")
        except Exception:
            pass
        try:
            with self.connection() as conn:
                conn.execute("""
                    UPDATE tasks SET process_tree_json = ?, updated_at = ?
                    WHERE task_id = ?;
                """, (raw_json, _utcnow_iso(), task_id))
        except Exception:
            pass

    def get_task_process_tree(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve stored process tree from disk or database."""
        clean_id = (task_id or "").strip()
        task_out_dir = get_output_dir() / clean_id
        tree_file = task_out_dir / "process_tree.json"
        if tree_file.exists():
            try:
                return json.loads(tree_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        task = self.get_task(clean_id)
        if task and task.get("process_tree_json"):
            try:
                return json.loads(task["process_tree_json"])
            except Exception:
                pass
        return None

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve task record by ID."""
        clean_id = (task_id or "").strip()
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM tasks WHERE task_id = ? COLLATE NOCASE;", (clean_id,))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["args"] = json.loads(data["args_json"])
            except Exception:
                data["args"] = []
            return data

    def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieve list of tasks ordered by start time descending."""
        query = "SELECT * FROM tasks"
        params: List[Any] = []

        if status:
            query += " WHERE status = ?"
            params.append(status)

        query += " ORDER BY start_time DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self.connection() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["args"] = json.loads(item["args_json"])
                except Exception:
                    item["args"] = []
                results.append(item)
            return results

    def count_active_tasks(self) -> int:
        """Count tasks currently in 'starting' or 'running' state."""
        with self.connection() as conn:
            cur = conn.execute("SELECT COUNT(*) as count FROM tasks WHERE status IN ('starting', 'running', 'terminating');")
            return cur.fetchone()["count"]

    def get_active_tasks(self) -> List[Dict[str, Any]]:
        """Retrieve all currently active tasks."""
        with self.connection() as conn:
            cur = conn.execute("SELECT * FROM tasks WHERE status IN ('starting', 'running', 'terminating') ORDER BY start_time ASC;")
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def record_event(self, task_id: str, event_type: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Record an audit event for a task."""
        now = _utcnow_iso()
        details_json = json.dumps(details) if details else None
        try:
            with self.connection() as conn:
                conn.execute("""
                    INSERT INTO task_events (task_id, event_type, timestamp, details_json)
                    VALUES (?, ?, ?, ?);
                """, (task_id, event_type, now, details_json))
        except Exception:
            # Audit event failure shouldn't crash the main process
            pass

    def reconcile_active_tasks(self) -> int:
        """Reconcile tasks after an unexpected shutdown.
        
        Marks any task marked 'starting' or 'running' whose PID is dead as 'interrupted'.
        """
        import psutil
        reconciled = 0
        active = self.get_active_tasks()
        for task in active:
            pid = task.get("pid")
            is_alive = False
            if pid:
                try:
                    proc = psutil.Process(pid)
                    if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                        is_alive = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    is_alive = False

            if not is_alive:
                self.update_task_status(
                    task["task_id"],
                    status="interrupted",
                    error_message="Process interrupted by abnormal system or supervisor exit."
                )
                reconciled += 1
        return reconciled

    def delete_task(self, task_id: str) -> bool:
        """Permanently delete a task and its stored log directory from history."""
        clean_id = (task_id or "").strip()
        task = self.get_task(clean_id)
        if not task:
            return False

        output_base = get_output_dir().resolve()
        task_dir = output_base / task["task_id"]
        if task_dir.exists() and output_base in task_dir.resolve().parents:
            try:
                shutil.rmtree(task_dir)
            except Exception:
                pass

        with self.connection() as conn:
            conn.execute("DELETE FROM task_events WHERE task_id = ? COLLATE NOCASE;", (task["task_id"],))
            conn.execute("DELETE FROM tasks WHERE task_id = ? COLLATE NOCASE;", (task["task_id"],))

        return True

    def cleanup_old_tasks(self, days: Optional[int] = None, keep_count: Optional[int] = None) -> Tuple[int, int]:
        """Safely clean up old completed, failed, or terminated tasks and their output files.
        
        Never deletes running/active tasks.
        Returns (records_deleted, files_deleted).
        """
        records_deleted = 0
        files_deleted = 0
        output_base = get_output_dir().resolve()

        with self.connection() as conn:
            # Only select tasks that are finished
            cur = conn.execute("""
                SELECT task_id, stdout_path, stderr_path FROM tasks
                WHERE status IN ('completed', 'failed', 'terminated', 'interrupted')
                ORDER BY start_time DESC;
            """)
            rows = cur.fetchall()

        tasks_to_delete = []
        if keep_count is not None and keep_count >= 0:
            tasks_to_delete = rows[keep_count:]
        elif days is not None and days >= 0:
            cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
            for row in rows:
                t = self.get_task(row["task_id"])
                if t and t.get("start_time"):
                    try:
                        st = datetime.fromisoformat(t["start_time"]).timestamp()
                        if st < cutoff:
                            tasks_to_delete.append(row)
                    except Exception:
                        pass
        else:
            # If neither keep_count nor days, delete all finished tasks
            tasks_to_delete = rows

        for task_row in tasks_to_delete:
            tid = task_row["task_id"]
            # Safely remove output files inside the managed directory
            task_dir = output_base / tid
            if task_dir.exists() and output_base in task_dir.resolve().parents:
                try:
                    shutil.rmtree(task_dir)
                    files_deleted += 1
                except Exception:
                    pass

            with self.connection() as conn:
                conn.execute("DELETE FROM tasks WHERE task_id = ?;", (tid,))
                records_deleted += 1

        return records_deleted, files_deleted

    def apply_auto_cleanup(self) -> Tuple[int, int]:
        """Apply automatic cleanup policy if enabled in advanced configuration."""
        from kerniq.config import get_advanced_config
        adv = get_advanced_config()
        if not adv.get("auto_cleanup_enabled"):
            return 0, 0

        days = adv.get("history_retention_days", 30)
        keep = adv.get("max_history_records", 100)
        return self.cleanup_old_tasks(days=days, keep_count=keep)

    # -----------------------------------------------------------------------
    # Activity Log Operations
    # -----------------------------------------------------------------------

    def add_app_log(self, action: str, details: Optional[str] = None) -> None:
        """Record an internal application action in the database and activity.log."""
        now = _utcnow_iso()
        try:
            with self.connection() as conn:
                conn.execute(
                    "INSERT INTO app_logs (timestamp, action, details) VALUES (?, ?, ?);",
                    (now, action, details)
                )
        except Exception:
            pass

        try:
            from kerniq.config import get_base_dir
            act_file = get_base_dir() / "activity.log"
            with open(act_file, "a", encoding="utf-8") as f:
                f.write(f"[{now}] {action}: {details or ''}\n")
        except Exception:
            pass

    def list_app_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recent application activity logs."""
        try:
            with self.connection() as conn:
                cur = conn.execute(
                    "SELECT id, timestamp, action, details FROM app_logs ORDER BY id DESC LIMIT ?;",
                    (limit,)
                )
                rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    return rows
        except Exception:
            pass

        # If app_logs table is empty, generate entries from task events and daemon log
        entries: List[Dict[str, Any]] = []
        try:
            with self.connection() as conn:
                cur = conn.execute(
                    "SELECT task_id, event_type, timestamp, details_json FROM task_events ORDER BY id DESC LIMIT ?;",
                    (limit,)
                )
                for r in cur.fetchall():
                    entries.append({
                        "id": None,
                        "timestamp": r["timestamp"],
                        "action": f"{r['event_type']} ({r['task_id']})",
                        "details": r["details_json"] or ""
                    })
        except Exception:
            pass
        return entries

    # -----------------------------------------------------------------------
    # Search / Query Operations
    # -----------------------------------------------------------------------

    def find_tasks(self, query: str) -> List[Dict[str, Any]]:
        """Search tasks by ID, command, status, exit code, or output log text."""
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        all_tasks = self.list_tasks(limit=500)
        matches = []
        clean_lower = clean_q.lower()

        for t in all_tasks:
            matched = False
            match_reason = ""
            tid = t.get("task_id", "")
            cmd = t.get("command", "")
            status = t.get("status", "")
            exit_code = t.get("exit_code")

            # 1. Match task ID
            if clean_lower in tid.lower() or clean_lower == tid.replace("KQ-", "").lower():
                matched = True
                match_reason = f"Matched Task ID: {tid}"

            # 2. Match command
            elif clean_lower in cmd.lower():
                matched = True
                match_reason = f"Matched Command: {cmd}"

            # 3. Match status / state
            elif clean_lower == status.lower():
                matched = True
                match_reason = f"Matched State: {status.upper()}"

            # 4. Match exit code
            elif (clean_q.isdigit() or (clean_q.startswith("-") and clean_q[1:].isdigit())) and exit_code is not None:
                if int(clean_q) == exit_code:
                    matched = True
                    match_reason = f"Matched Exit Code: {exit_code}"

            # 5. Search in output log files
            if not matched:
                out_path = t.get("stdout_path")
                err_path = t.get("stderr_path")
                for p_str, label in ((out_path, "stdout"), (err_path, "stderr")):
                    if p_str and Path(p_str).exists():
                        try:
                            with open(p_str, "r", encoding="utf-8", errors="replace") as f:
                                for line in f:
                                    if clean_lower in line.lower():
                                        matched = True
                                        match_reason = f"Matched in {label}: \"{line.strip()[:80]}\""
                                        break
                        except Exception:
                            pass
                    if matched:
                        break

            if matched:
                t_copy = dict(t)
                t_copy["match_reason"] = match_reason
                matches.append(t_copy)

        return matches

    def is_shell_active(self) -> bool:
        """Check if an interactive shell session is currently running."""
        return is_shell_active()

    def set_shell_active(self) -> None:
        """Mark interactive shell session active."""
        set_shell_active()

    def clear_shell_active(self) -> None:
        """Clear interactive shell session active marker."""
        clear_shell_active()

# ---------------------------------------------------------------------------
# Shell State Helpers
# ---------------------------------------------------------------------------

SHELL_ACTIVE_FILE = "shell.active"

def get_shell_active_file() -> Path:
    from kerniq.config import get_base_dir
    return get_base_dir() / SHELL_ACTIVE_FILE

def set_shell_active() -> None:
    """Record that an interactive shell session is currently running."""
    import os
    path = get_shell_active_file()
    path.write_text(str(os.getpid()), encoding="utf-8")

def clear_shell_active() -> None:
    """Clear interactive shell session active marker."""
    path = get_shell_active_file()
    path.unlink(missing_ok=True)

def is_shell_active() -> bool:
    """Check if an interactive shell session is genuinely alive."""
    import psutil
    path = get_shell_active_file()
    if not path.exists():
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        proc = psutil.Process(pid)
        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
            return True
    except Exception:
        pass
    path.unlink(missing_ok=True)
    return False

