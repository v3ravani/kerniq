"""Task supervisor: process launching, real-time output capture, metrics, and lifecycle management."""

import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

from kerniq.exceptions import ProcessError, TaskNotFoundError
from kerniq.metrics import ProcessTreeMetrics, ThresholdEvaluator
from kerniq.notifications import NotificationService
from kerniq.proc_tree import ProcessTreeTracker
from kerniq.storage import Storage

PROMPT_REGEX = re.compile(
    r"(?:"
    r"\[\s*[yY]\s*/\s*[nN]\s*\]|"
    r"\(\s*[yY]\s*/\s*[nN]\s*\)|"
    r"\[\s*[yY]es\s*/\s*[nN]o\s*\]|"
    r"\(\s*[yY]es\s*/\s*[nN]o\s*\)|"
    r"\b(?:enter|input|type|provide|confirm|specify|choose|select|password|passphrase|token|credential|secret|username|login)\b.*[:\?]\s*$|"
    r"\b(?:continue|proceed|overwrite|delete|remove|retry|abort)\b.*\?\s*$|"
    r"\bpress\s+(?:any\s+key|enter|return)\s+to\s+continue|"
    r"\bare\s+you\s+sure\b.*\?\s*$|"
    r"\bdo\s+you\s+want\s+to\b.*\?\s*$"
    r")",
    re.IGNORECASE
)

def generate_task_id(storage: Optional[Storage] = None) -> str:
    """Generate a unique, short, human-friendly task ID (e.g. KQ-1042).
    
    Format: KQ-XXXX where XXXX is strictly numeric (1000-9999).
    """
    st = storage or Storage()
    while True:
        token = secrets.randbelow(9000) + 1000
        task_id = f"KQ-{token}"
        if not st.get_task(task_id):
            return task_id

def format_duration(seconds: float) -> str:
    """Format duration in seconds to HH:MM:SS format."""
    total_sec = int(round(seconds))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"

class TaskSupervisor:
    """Oversees execution, streaming output capture, resource monitoring, and termination."""

    def __init__(self, storage: Optional[Storage] = None, notifications: Optional[NotificationService] = None):
        self.storage = storage or Storage()
        self.notifications = notifications or NotificationService()
        self._active_processes: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def run_command(
        self,
        command_args: List[str],
        cwd: Optional[str] = None,
        tee_to_stdout: bool = True,
        on_line: Optional[Callable[[str, str], None]] = None,
        task_id: Optional[str] = None
    ) -> Tuple[str, int]:
        """Execute command, capture output streams, measure resources, and return (task_id, exit_code).
        
        Args:
            command_args: Executable and arguments.
            cwd: Working directory (defaults to current directory).
            tee_to_stdout: Whether to stream output live to terminal.
            on_line: Optional callback for each line: (stream_name, line).
            task_id: Optional pre-generated task ID (generated if not provided).
        """
        if not command_args:
            raise ProcessError("No command provided to execute.")

        task_id = task_id or generate_task_id(self.storage)
        working_dir = cwd or os.getcwd()
        cmd_str = " ".join(command_args)

        # 1. Create task record in starting state
        task = self.storage.create_task(
            task_id=task_id,
            command=cmd_str,
            args=command_args,
            cwd=working_dir,
            status="starting"
        )

        stdout_file_path = Path(task["stdout_path"])
        stderr_file_path = Path(task["stderr_path"])

        start_time_iso = task["start_time"]
        start_time_mono = time.monotonic()

        # 2. Launch process safely
        try:
            # On POSIX, start a new process group so children can be signaled reliably
            popen_kwargs: Dict[str, Any] = {
                "args": command_args,
                "cwd": working_dir,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1
            }
            if sys.platform != "win32":
                popen_kwargs["start_new_session"] = True

            proc = subprocess.Popen(**popen_kwargs)
        except Exception as e:
            err_msg = str(e)
            duration = time.monotonic() - start_time_mono
            self.storage.finish_task(
                task_id=task_id,
                status="failed",
                exit_code=1,
                duration_seconds=duration,
                error_message=f"Launch failure: {err_msg}"
            )
            self.notifications.notify_task_failed(
                task_id=task_id,
                command=cmd_str,
                duration_str=format_duration(duration),
                exit_code=1,
                error_msg=f"Launch failure: {err_msg}"
            )
            raise ProcessError(f"Failed to launch process '{command_args[0]}': {err_msg}")

        # Start ProcessTreeTracker immediately to capture short-lived child processes
        tree_tracker: Optional[ProcessTreeTracker] = None
        try:
            tree_tracker = ProcessTreeTracker(
                root_pid=proc.pid,
                task_id=task_id,
                storage=self.storage,
                output_dir=stdout_file_path.parent
            )
            tree_tracker.start()
        except Exception:
            tree_tracker = None

        # Update status to running with real PID
        with self._lock:
            self._active_processes[task_id] = proc
        self.storage.update_task_status(task_id, status="running", pid=proc.pid)

        # Send task started notification
        self.notifications.notify_task_started(
            task_id=task_id,
            command=cmd_str,
            start_time=start_time_iso
        )

        # 3. Setup output drain threads (prevent pipe deadlock)
        stdout_f = open(stdout_file_path, "w", encoding="utf-8", buffering=1)
        stderr_f = open(stderr_file_path, "w", encoding="utf-8", buffering=1)

        input_alert_sent = [False]
        last_stream_activity = [time.monotonic()]
        pending_buf = {"stdout": "", "stderr": ""}
        last_completed_lines: List[str] = []

        def _check_and_notify_prompt(text: str) -> None:
            if not text:
                return
            cleaned = text.strip()
            if PROMPT_REGEX.search(cleaned) and not input_alert_sent[0]:
                input_alert_sent[0] = True
                self.notifications.notify_task_input_required(task_id, cmd_str)

        def _drain_stream(stream, file_handle, stream_name: str):
            try:
                while True:
                    ch = stream.read(1)
                    if not ch:
                        break

                    file_handle.write(ch)
                    file_handle.flush()
                    if tee_to_stdout:
                        target = sys.stderr if stream_name == "stderr" else sys.stdout
                        target.write(ch)
                        target.flush()

                    last_stream_activity[0] = time.monotonic()
                    if ch == "\n":
                        full_line = pending_buf[stream_name] + "\n"
                        pending_buf[stream_name] = ""
                        last_completed_lines.append(full_line)
                        if len(last_completed_lines) > 5:
                            last_completed_lines.pop(0)
                        if on_line:
                            on_line(stream_name, full_line)
                        input_alert_sent[0] = False
                    else:
                        pending_buf[stream_name] += ch
                        if len(pending_buf[stream_name]) > 300:
                            pending_buf[stream_name] = pending_buf[stream_name][-300:]
                        _check_and_notify_prompt(pending_buf[stream_name])
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
                try:
                    file_handle.close()
                except Exception:
                    pass

        t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, stdout_f, "stdout"), daemon=True)
        t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, stderr_f, "stderr"), daemon=True)
        t_out.start()
        t_err.start()

        # 4. Resource monitoring thread
        stop_metrics = threading.Event()
        peak_cpu = [0.0]
        peak_mem = [0.0]
        cpu_samples: List[float] = []
        duration_alert_sent = [False]
        battery_alert_sent = [False]

        def _monitor_resources():
            from kerniq.config import get_rules
            from kerniq.metrics import get_battery_info
            rules = get_rules()
            evaluator = ThresholdEvaluator(rules)
            interval = float(rules.get("sampling_interval_seconds", 2.0))

            while not stop_metrics.wait(timeout=min(interval, 0.8)):
                rules = get_rules()
                evaluator.rules = rules

                # 4a. Duration threshold check (one alert per task)
                if rules.get("duration_threshold") and not duration_alert_sent[0]:
                    dur_thresh = float(rules.get("duration_seconds", 300.0))
                    elapsed = time.monotonic() - start_time_mono
                    if elapsed >= dur_thresh:
                        duration_alert_sent[0] = True
                        self.notifications.notify_duration_alert(task_id, elapsed, dur_thresh)

                # 4b. Battery threshold check (one alert per monitoring session)
                if rules.get("battery_threshold") and not battery_alert_sent[0]:
                    bat_thresh = float(rules.get("battery_percent", 20.0))
                    bat_info = get_battery_info()
                    if bat_info.get("available") and bat_info.get("percent") is not None:
                        # Only alert if battery is at or below threshold and discharging
                        if bat_info["percent"] <= bat_thresh and not bat_info.get("power_plugged"):
                            battery_alert_sent[0] = True
                            self.notifications.notify_battery_alert(bat_info["percent"], bat_thresh)

                # 4c. Input-required check if process paused while waiting for user input
                if rules.get("task_input_required", True) and not input_alert_sent[0]:
                    if proc.poll() is None and (time.monotonic() - last_stream_activity[0]) >= 0.8:
                        candidate = pending_buf["stdout"] + " " + pending_buf["stderr"]
                        if last_completed_lines:
                            candidate += " " + last_completed_lines[-1]
                        _check_and_notify_prompt(candidate)

                metrics = ProcessTreeMetrics.get_metrics(proc.pid)
                if not metrics:
                    break

                cpu_pct = metrics["cpu_percent"]
                mem_mb = metrics["memory_rss_mb"]

                if cpu_pct > 0:
                    cpu_samples.append(cpu_pct)
                if cpu_pct > peak_cpu[0]:
                    peak_cpu[0] = cpu_pct
                if mem_mb > peak_mem[0]:
                    peak_mem[0] = mem_mb

                cpu_alert, mem_alert = evaluator.evaluate(metrics)
                if cpu_alert:
                    self.notifications.notify_cpu_alert(task_id, cpu_alert)
                if mem_alert:
                    self.notifications.notify_mem_alert(task_id, mem_alert)

        t_mon = threading.Thread(target=_monitor_resources, daemon=True)
        t_mon.start()

        # 5. Wait for process completion
        try:
            return_code = proc.wait()
        except KeyboardInterrupt:
            self.terminate_task(task_id)
            return_code = proc.poll() or 130
        finally:
            stop_metrics.set()
            if tree_tracker:
                try:
                    tree_tracker.stop(root_exit_code=return_code if 'return_code' in locals() else None)
                except Exception:
                    pass
            t_out.join(timeout=2.0)
            t_err.join(timeout=2.0)
            with self._lock:
                self._active_processes.pop(task_id, None)

        duration = time.monotonic() - start_time_mono
        duration_formatted = format_duration(duration)

        # Check if task was terminated remotely
        current_record = self.storage.get_task(task_id)
        if current_record and current_record["status"] in ("terminating", "terminated"):
            final_status = "terminated"
        elif return_code == 0:
            final_status = "completed"
        else:
            final_status = "failed"

        # Finalize storage record
        tree_json_str = None
        if tree_tracker:
            try:
                import json
                tree_json_str = json.dumps(tree_tracker.get_tree_dict())
            except Exception:
                tree_json_str = None

        cpu_avg = round(sum(cpu_samples) / len(cpu_samples), 1) if cpu_samples else None

        err_msg = current_record.get("error_message") if current_record else None
        if not err_msg and final_status == "failed":
            if return_code != 0:
                err_msg = "Build process exited with a non-zero status." if "build" in cmd_str.lower() else f"Process exited with exit code {return_code}."
        elif not err_msg and final_status == "terminated":
            err_msg = "Task terminated by user."

        fin = self.storage.finish_task(
            task_id=task_id,
            status=final_status,
            exit_code=return_code,
            duration_seconds=duration,
            error_message=err_msg,
            cpu_peak_percent=peak_cpu[0],
            memory_peak_mb=peak_mem[0],
            process_tree_json=tree_json_str,
            cpu_avg_percent=cpu_avg
        )

        # Send completion / error notifications
        if final_status == "completed":
            self.notifications.notify_task_completed(
                task_id=task_id,
                command=cmd_str,
                duration_str=duration_formatted,
                stdout_size=fin.get("stdout_size", 0),
                stderr_size=fin.get("stderr_size", 0)
            )
        elif final_status == "failed":
            self.notifications.notify_task_failed(
                task_id=task_id,
                command=cmd_str,
                duration_str=duration_formatted,
                exit_code=return_code
            )

        return task_id, return_code

    def terminate_task(self, task_id: str) -> Tuple[bool, str]:
        """Terminate a task and all of its descendants safely.
        
        Returns (success: bool, message: str).
        """
        task = self.storage.get_task(task_id)
        if not task:
            return False, f"Task '{task_id}' not found."

        if task["status"] not in ("starting", "running", "terminating"):
            return False, f"Task '{task_id}' is not active (current status: {task['status']})."

        self.storage.update_task_status(task_id, status="terminating")

        pid = task.get("pid")
        if not pid:
            self.storage.update_task_status(task_id, status="terminated")
            return True, f"Task '{task_id}' had no PID and was marked terminated."

        # Attempt process-tree termination
        terminated_ok = False
        try:
            parent = psutil.Process(pid)
            children = []
            try:
                children = parent.children(recursive=True)
            except Exception:
                pass

            # Terminate children first
            for c in children:
                try:
                    c.terminate()
                except Exception:
                    pass

            # Terminate parent
            parent.terminate()

            # Wait for processes to exit
            gone, alive = psutil.wait_procs(children + [parent], timeout=3.0)

            # If still alive, kill forcefully
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass

            # Check parent alive
            terminated_ok = not parent.is_running()
        except psutil.NoSuchProcess:
            terminated_ok = True
        except Exception:
            # Fallback platform strategies
            if sys.platform == "win32":
                res = os.system(f"taskkill /F /T /PID {pid} >nul 2>&1")
                terminated_ok = (res == 0)
            else:
                try:
                    import signal
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    terminated_ok = True
                except Exception:
                    terminated_ok = False

        if terminated_ok:
            self.storage.update_task_status(task_id, status="terminated")
            return True, f"Task '{task_id}' (PID {pid}) and its process tree were successfully terminated."
        else:
            return False, f"Termination signal sent to task '{task_id}', but process exit could not be confirmed."
