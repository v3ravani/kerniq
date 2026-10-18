"""Process tree discovery, tracking, and hierarchical formatting for Kerniq tasks."""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import psutil

from kerniq.terminal import (
    blue,
    cyan,
    gray,
    green,
    red,
    status_badge,
    yellow,
)

# Win32 Process Constants
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000


def _extract_basename(path_str: str) -> str:
    """Extract the filename/basename from a path across all platforms.
    
    Handles both Windows backslashes and POSIX forward slashes, regardless of the host OS.
    """
    clean = path_str.replace("\\", "/").rstrip("/")
    if "/" in clean:
        base = clean.rsplit("/", 1)[-1]
        return base or path_str
    return clean or path_str


def get_process_display_name(name: str, cmdline: Optional[List[str]] = None, is_root: bool = False) -> str:
    """Determine a clear, readable display name for a process.
    
    If it is an interpreter (Python, Node) running a script, returns the script basename.
    Otherwise returns the executable name.
    """
    if not name:
        return "unknown"

    if is_root:
        return name

    name_lower = name.lower()
    if "python" in name_lower and cmdline and len(cmdline) > 1:
        for arg in cmdline[1:]:
            if arg.startswith("-"):
                continue
            if arg.endswith(".py") or "\\" in arg or "/" in arg:
                base = _extract_basename(arg)
                if base:
                    return base
    elif "node" in name_lower and cmdline and len(cmdline) > 1:
        for arg in cmdline[1:]:
            if arg.startswith("-"):
                continue
            if arg.endswith((".js", ".mjs", ".cjs", ".ts")) or "\\" in arg or "/" in arg:
                base = _extract_basename(arg)
                if base:
                    return base

    return name


class ProcessTreeTracker:
    """Monitors a task's root process and all child descendants in real-time.
    
    Maintains parent-child links, tracks short-lived subprocesses via fast sampling,
    protects against PID reuse with creation timestamps and Win32 handles,
    and captures process exit codes and resource usage.
    """

    def __init__(
        self,
        root_pid: int,
        task_id: str,
        storage: Optional[Any] = None,
        output_dir: Optional[Path] = None,
        sampling_interval: float = 0.01
    ):
        self.root_pid = root_pid
        self.task_id = task_id
        self.storage = storage
        self.output_dir = output_dir
        self.sampling_interval = sampling_interval

        self.nodes: Dict[int, Dict[str, Any]] = {}
        self._win_handles: Dict[int, Any] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._kernel32 = None
        if sys.platform == "win32":
            try:
                import ctypes
                self._kernel32 = ctypes.windll.kernel32
            except Exception:
                self._kernel32 = None

        self._init_root()

    def _open_win_handle(self, pid: int) -> Optional[Any]:
        if not self._kernel32:
            return None
        try:
            h = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
            if h:
                return h
        except Exception:
            pass
        return None

    def _init_root(self) -> None:
        try:
            p = psutil.Process(self.root_pid)
            name = p.name()
            try:
                cmdline = p.cmdline()
            except Exception:
                cmdline = []
            create_time = p.create_time()
            try:
                ppid = p.ppid()
            except Exception:
                ppid = None

            h = self._open_win_handle(self.root_pid)
            if h:
                self._win_handles[self.root_pid] = h

            self.nodes[self.root_pid] = {
                "pid": self.root_pid,
                "ppid": ppid,
                "name": name,
                "display_name": get_process_display_name(name, cmdline, is_root=True),
                "cmdline": cmdline,
                "status": "RUNNING",
                "create_time": create_time,
                "start_time": time.time(),
                "end_time": None,
                "duration_seconds": None,
                "exit_code": None,
                "cpu_percent": 0.0,
                "memory_rss_mb": 0.0,
                "children": []
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.nodes[self.root_pid] = {
                "pid": self.root_pid,
                "ppid": None,
                "name": "unknown",
                "display_name": "unknown",
                "cmdline": [],
                "status": "RUNNING",
                "create_time": time.time(),
                "start_time": time.time(),
                "end_time": None,
                "duration_seconds": None,
                "exit_code": None,
                "cpu_percent": 0.0,
                "memory_rss_mb": 0.0,
                "children": []
            }

    def start(self) -> None:
        """Start the background tracking loop."""
        try:
            self.sample()
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run_loop, name=f"proc-tree-{self.task_id}", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        # Initial sample to capture early child processes
        try:
            self.sample()
        except Exception:
            pass

        while not self._stop_event.wait(timeout=self.sampling_interval):
            try:
                self.sample()
            except Exception:
                pass

    def sample(self) -> None:
        """Sample current process hierarchy, discover children, and record metrics."""
        with self._lock:
            try:
                root_proc = psutil.Process(self.root_pid)
                if not root_proc.is_running():
                    return
                # PID reuse guard
                if abs(root_proc.create_time() - self.nodes[self.root_pid]["create_time"]) > 2.0:
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return

            # 1. Update root metrics
            try:
                self.nodes[self.root_pid]["cpu_percent"] = root_proc.cpu_percent(interval=None)
                self.nodes[self.root_pid]["memory_rss_mb"] = round(root_proc.memory_info().rss / (1024 * 1024), 1)
            except Exception:
                pass

            # 2. Discover children
            try:
                active_children = root_proc.children(recursive=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                active_children = []

            active_pids: Set[int] = set()

            for c in active_children:
                try:
                    c_pid = c.pid
                    c_create_time = c.create_time()
                    active_pids.add(c_pid)

                    if c_pid not in self.nodes or self.nodes[c_pid]["create_time"] != c_create_time:
                        # New child discovered
                        c_name = c.name()
                        try:
                            c_cmdline = c.cmdline()
                        except Exception:
                            c_cmdline = []
                        try:
                            c_ppid = c.ppid()
                        except Exception:
                            c_ppid = self.root_pid

                        disp_name = get_process_display_name(c_name, c_cmdline, is_root=False)

                        h = self._open_win_handle(c_pid)
                        if h:
                            self._win_handles[c_pid] = h

                        self.nodes[c_pid] = {
                            "pid": c_pid,
                            "ppid": c_ppid,
                            "name": c_name,
                            "display_name": disp_name,
                            "cmdline": c_cmdline,
                            "status": "RUNNING",
                            "create_time": c_create_time,
                            "start_time": time.time(),
                            "end_time": None,
                            "duration_seconds": None,
                            "exit_code": None,
                            "cpu_percent": 0.0,
                            "memory_rss_mb": 0.0,
                            "children": []
                        }

                        # Attach to parent's children list
                        parent_node = self.nodes.get(c_ppid)
                        if parent_node:
                            if c_pid not in parent_node["children"]:
                                parent_node["children"].append(c_pid)
                        else:
                            # Direct or indirect descendant; link to root if parent unknown
                            if c_pid not in self.nodes[self.root_pid]["children"]:
                                self.nodes[self.root_pid]["children"].append(c_pid)
                    else:
                        # Existing child: update resource usage
                        try:
                            self.nodes[c_pid]["cpu_percent"] = c.cpu_percent(interval=None)
                            self.nodes[c_pid]["memory_rss_mb"] = round(c.memory_info().rss / (1024 * 1024), 1)
                        except Exception:
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # 3. Check for terminated children
            for pid, node in list(self.nodes.items()):
                if pid == self.root_pid:
                    continue
                if node["status"] == "RUNNING" and pid not in active_pids:
                    self._mark_terminated(pid)

            self._save_tree()

    def _mark_terminated(self, pid: int, exit_code: Optional[int] = None) -> None:
        node = self.nodes.get(pid)
        if not node or node["status"] == "TERMINATED":
            return

        node["status"] = "TERMINATED"
        if not node.get("end_time"):
            node["end_time"] = time.time()
            node["duration_seconds"] = round(max(0.0, node["end_time"] - node["start_time"]), 2)

        if exit_code is not None:
            node["exit_code"] = exit_code
        elif pid in self._win_handles and self._kernel32:
            import ctypes
            h = self._win_handles[pid]
            code = ctypes.c_ulong()
            if self._kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                # STILL_ACTIVE = 259; only record if genuinely exited
                if code.value != 259:
                    node["exit_code"] = code.value
            self._kernel32.CloseHandle(h)
            self._win_handles.pop(pid, None)

    def stop(self, root_exit_code: Optional[int] = None) -> None:
        """Stop tracking, finalize all process states and exit codes, and persist tree."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        with self._lock:
            # Mark root terminated
            self._mark_terminated(self.root_pid, exit_code=root_exit_code)

            # Mark any remaining running children terminated
            for pid, node in list(self.nodes.items()):
                if node["status"] == "RUNNING":
                    self._mark_terminated(pid)

            # Close any remaining open Win32 handles
            if self._kernel32:
                for pid, h in list(self._win_handles.items()):
                    try:
                        self._kernel32.CloseHandle(h)
                    except Exception:
                        pass
                self._win_handles.clear()

            self._save_tree()

    def get_tree_dict(self) -> Dict[str, Any]:
        """Return the serializable process tree dictionary."""
        return {
            "task_id": self.task_id,
            "root_pid": self.root_pid,
            "nodes": {str(k): v for k, v in self.nodes.items()}
        }

    def _save_tree(self) -> None:
        """Persist current tree to disk."""
        data = self.get_tree_dict()
        if self.output_dir:
            try:
                tree_file = Path(self.output_dir) / "process_tree.json"
                tree_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass


def update_live_tree(root_pid: int, current_tree: Optional[Dict[str, Any]], task_id: str) -> Optional[Dict[str, Any]]:
    """Update active tree with live CPU and memory values for running processes."""
    if not root_pid:
        return current_tree

    nodes: Dict[str, Any] = {}
    if current_tree and current_tree.get("nodes"):
        nodes = dict(current_tree["nodes"])

    try:
        if not psutil.pid_exists(root_pid):
            return current_tree

        root_proc = psutil.Process(root_pid)
        if not root_proc.is_running() or root_proc.status() == psutil.STATUS_ZOMBIE:
            return current_tree

        root_str = str(root_pid)
        if root_str not in nodes:
            try:
                cmdline = root_proc.cmdline()
            except Exception:
                cmdline = []
            name = root_proc.name()
            nodes[root_str] = {
                "pid": root_pid,
                "ppid": root_proc.ppid(),
                "name": name,
                "display_name": get_process_display_name(name, cmdline, is_root=True),
                "cmdline": cmdline,
                "status": "RUNNING",
                "create_time": root_proc.create_time(),
                "start_time": time.time(),
                "end_time": None,
                "duration_seconds": None,
                "exit_code": None,
                "cpu_percent": 0.0,
                "memory_rss_mb": 0.0,
                "children": []
            }

        # Update root live metrics
        try:
            nodes[root_str]["cpu_percent"] = root_proc.cpu_percent(interval=None)
            nodes[root_str]["memory_rss_mb"] = round(root_proc.memory_info().rss / (1024 * 1024), 1)
        except Exception:
            pass

        # Live children
        try:
            active_kids = root_proc.children(recursive=True)
        except Exception:
            active_kids = []

        active_pids = {k.pid for k in active_kids}

        for k in active_kids:
            try:
                k_str = str(k.pid)
                if k_str not in nodes:
                    try:
                        k_cmd = k.cmdline()
                    except Exception:
                        k_cmd = []
                    k_name = k.name()
                    try:
                        k_ppid = k.ppid()
                    except Exception:
                        k_ppid = root_pid

                    nodes[k_str] = {
                        "pid": k.pid,
                        "ppid": k_ppid,
                        "name": k_name,
                        "display_name": get_process_display_name(k_name, k_cmd, is_root=False),
                        "cmdline": k_cmd,
                        "status": "RUNNING",
                        "create_time": k.create_time(),
                        "start_time": time.time(),
                        "end_time": None,
                        "duration_seconds": None,
                        "exit_code": None,
                        "cpu_percent": k.cpu_percent(interval=None),
                        "memory_rss_mb": round(k.memory_info().rss / (1024 * 1024), 1),
                        "children": []
                    }
                    parent_str = str(k_ppid)
                    if parent_str in nodes and k.pid not in nodes[parent_str]["children"]:
                        nodes[parent_str]["children"].append(k.pid)
                    elif k.pid not in nodes[root_str]["children"]:
                        nodes[root_str]["children"].append(k.pid)
                else:
                    nodes[k_str]["status"] = "RUNNING"
                    nodes[k_str]["cpu_percent"] = k.cpu_percent(interval=None)
                    nodes[k_str]["memory_rss_mb"] = round(k.memory_info().rss / (1024 * 1024), 1)
            except Exception:
                continue

        # Mark any missing children as TERMINATED
        for pid_str, node in nodes.items():
            if int(pid_str) == root_pid:
                continue
            if node.get("status") == "RUNNING" and int(pid_str) not in active_pids:
                node["status"] = "TERMINATED"
                if not node.get("end_time"):
                    node["end_time"] = time.time()
                    if node.get("start_time"):
                        node["duration_seconds"] = round(max(0.0, node["end_time"] - node["start_time"]), 2)

    except Exception:
        pass

    return {
        "task_id": task_id,
        "root_pid": root_pid,
        "nodes": nodes
    }


def _format_node_line(node: Dict[str, Any], prefix: str = "", is_root: bool = False) -> str:
    name = node.get("display_name") or node.get("name") or "unknown"
    pid = node.get("pid", 0)
    status = (node.get("status") or "UNKNOWN").upper()

    if status == "RUNNING":
        status_text = green("RUNNING")
    elif status in ("TERMINATED", "COMPLETED", "EXITED"):
        exit_code = node.get("exit_code")
        if exit_code is not None:
            if exit_code == 0:
                status_text = f"{green('TERMINATED')} {gray(f'(exit: {exit_code})')}"
            else:
                status_text = f"{red('TERMINATED')} {red(f'(exit: {exit_code})')}"
        else:
            status_text = gray("TERMINATED")
    else:
        status_text = status_badge(status)

    details_parts: List[str] = []
    if status == "RUNNING":
        cpu = node.get("cpu_percent")
        mem = node.get("memory_rss_mb")
        if cpu is not None and cpu > 0:
            details_parts.append(f"CPU: {cpu:.1f}%")
        if mem is not None and mem > 0:
            details_parts.append(f"Mem: {mem:.1f} MB")
    else:
        dur = node.get("duration_seconds")
        if dur is not None and dur > 0:
            total_sec = int(round(dur))
            hrs = total_sec // 3600
            mins = (total_sec % 3600) // 60
            secs = total_sec % 60
            details_parts.append(f"duration: {hrs:02d}:{mins:02d}:{secs:02d}")

    details_str = ""
    if details_parts:
        details_str = "  " + gray(f"({', '.join(details_parts)})")

    return f"{prefix}{name}  [PID {pid}]  {status_text}{details_str}"


def render_process_tree(task: Dict[str, Any], tree_data: Optional[Dict[str, Any]]) -> str:
    """Render a polished, aligned process tree following Kerniq CLI aesthetic standards."""
    lines: List[str] = []

    lines.append(cyan("Kerniq | Process Tree"))
    lines.append("")

    task_id = task.get("task_id", "Unknown")
    lines.append(f"{'Task ID':<11}{cyan(task_id)}")
    lines.append(f"{'Command':<11}{task.get('command', 'N/A')}")

    status_raw = (task.get("status") or "unknown").upper()
    code = task.get("exit_code")
    if status_raw == "RUNNING":
        status_display = green("RUNNING")
    elif status_raw == "COMPLETED":
        code_str = f" (exit code: {code})" if code is not None else ""
        status_display = f"{green('COMPLETED')}{code_str}"
    elif status_raw in ("FAILED", "ERROR"):
        code_str = f" (exit code: {code})" if code is not None else ""
        status_display = f"{red('FAILED')}{code_str}"
    elif status_raw in ("TERMINATED", "TERMINATING"):
        code_str = f" (exit code: {code})" if code is not None else ""
        status_display = f"{yellow(status_raw)}{code_str}"
    else:
        status_display = status_badge(status_raw)
    lines.append(f"{'Status':<11}{status_display}")

    root_pid = task.get("pid")
    root_pid_str = str(root_pid) if root_pid else "N/A"
    lines.append(f"{'Root PID':<11}{root_pid_str}")

    dur = task.get("duration_seconds")
    if dur is not None:
        total_sec = int(round(dur))
        hrs = total_sec // 3600
        mins = (total_sec % 3600) // 60
        secs = total_sec % 60
        lines.append(f"{'Duration':<11}{hrs:02d}:{mins:02d}:{secs:02d}")

    lines.append("")
    lines.append(blue("PROCESS HIERARCHY"))

    nodes_raw = tree_data.get("nodes") if tree_data else None
    if not nodes_raw:
        lines.append(gray("(Process tree information is unavailable for this task.)"))
        lines.append("")
        lines.append(blue("SUMMARY"))
        lines.append("Tracked processes: 0 (Information unavailable)")
        return "\n".join(lines)

    # Convert keys to int for clean lookup
    nodes: Dict[int, Dict[str, Any]] = {}
    for k, v in nodes_raw.items():
        try:
            nodes[int(k)] = v
        except Exception:
            continue

    if not nodes:
        lines.append(gray("(Process tree information is unavailable for this task.)"))
        lines.append("")
        lines.append(blue("SUMMARY"))
        lines.append("Tracked processes: 0 (Information unavailable)")
        return "\n".join(lines)

    # Find root node
    target_root_pid = tree_data.get("root_pid") or root_pid
    root_node = None
    if target_root_pid and target_root_pid in nodes:
        root_node = nodes[target_root_pid]
    else:
        # Fallback: pick node whose ppid is not in nodes, or first node
        candidates = [n for n in nodes.values() if n.get("ppid") not in nodes]
        root_node = candidates[0] if candidates else next(iter(nodes.values()))

    visited: Set[int] = set()

    def _render_children(node: Dict[str, Any], prefix: str = "") -> None:
        p_id = node.get("pid")
        if p_id is not None:
            visited.add(p_id)

        child_pids = [c for c in node.get("children", []) if c in nodes]
        for idx, c_pid in enumerate(child_pids):
            c_node = nodes[c_pid]
            is_last = (idx == len(child_pids) - 1)
            branch = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")
            lines.append(_format_node_line(c_node, prefix=prefix + branch, is_root=False))
            _render_children(c_node, prefix=child_prefix)

    # 1. Print root
    lines.append(_format_node_line(root_node, prefix="", is_root=True))
    _render_children(root_node, prefix="")

    # 2. Print any unvisited nodes as direct branches
    unvisited = [p for p in nodes if p not in visited]
    for idx, u_pid in enumerate(unvisited):
        u_node = nodes[u_pid]
        is_last = (idx == len(unvisited) - 1)
        branch = "└── " if is_last else "├── "
        lines.append(_format_node_line(u_node, prefix=branch, is_root=False))
        _render_children(u_node, prefix="    " if is_last else "│   ")

    # SUMMARY
    lines.append("")
    lines.append(blue("SUMMARY"))
    total_count = len(nodes)
    running_count = sum(1 for n in nodes.values() if (n.get("status") or "").upper() == "RUNNING")
    term_count = total_count - running_count

    if running_count > 0 and term_count > 0:
        lines.append(f"Tracked processes: {total_count} ({running_count} running, {term_count} terminated)")
    else:
        lines.append(f"Tracked processes: {total_count}")

    # Peak resource consumption from task record if available
    peak_cpu = task.get("cpu_peak_percent")
    peak_mem = task.get("memory_peak_mb")
    if peak_cpu is not None and peak_cpu > 0:
        lines.append(f"Peak CPU:          {peak_cpu:.1f}%")
    if peak_mem is not None and peak_mem > 0:
        lines.append(f"Peak Memory:       {peak_mem:.1f} MB")

    return "\n".join(lines)


def display_task_tree(task_id: str, storage: Optional[Any] = None) -> int:
    """Load and print the process tree for the specified task ID."""
    from kerniq.storage import Storage
    storage = storage or Storage()

    clean_id = (task_id or "").strip()
    if not clean_id:
        print(red("Error: Task ID required. Usage: kerniq tree <id>"))
        return 1

    task = storage.get_task(clean_id)
    if not task:
        print(red(f"Error: Task '{clean_id}' not found."))
        return 1

    tree_data = storage.get_task_process_tree(clean_id)
    if task.get("status") == "running" and task.get("pid"):
        tree_data = update_live_tree(task["pid"], tree_data, clean_id)

    rendered = render_process_tree(task, tree_data)
    print(rendered)
    return 0


def format_process_events(task: Dict[str, Any], tree_data: Optional[Dict[str, Any]]) -> str:
    """Format chronological process lifecycle events (started, spawned, exited)."""
    events = []
    task_id = task.get("task_id", "")
    root_pid = task.get("pid")

    nodes = {}
    if tree_data and isinstance(tree_data, dict) and "nodes" in tree_data:
        nodes = tree_data["nodes"]

    if nodes:
        for pid_str, node in nodes.items():
            try:
                pid = int(node.get("pid") or pid_str)
            except Exception:
                pid = node.get("pid") or pid_str
            disp_name = node.get("display_name") or node.get("name") or "process"
            is_root = (pid == root_pid) or (node.get("ppid") is None)

            # Start / Spawn event
            st = node.get("start_time") or node.get("create_time")
            if st:
                action = "started" if is_root else "spawned"
                events.append({
                    "ts": float(st),
                    "text": f"{disp_name} {action} PID {pid}"
                })

            # Exit event
            et = node.get("end_time")
            status = node.get("status")
            code = node.get("exit_code")
            if et or status == "TERMINATED" or code is not None:
                exit_ts = float(et) if et else (float(st) + float(node.get("duration_seconds") or 0.1) if st else None)
                if exit_ts:
                    code_str = str(code) if code is not None else (str(task.get("exit_code")) if is_root and task.get("exit_code") is not None else "0")
                    events.append({
                        "ts": exit_ts,
                        "text": f"{disp_name} exited Code {code_str}"
                    })
    else:
        st_iso = task.get("start_time")
        cmd = task.get("command", "task")
        exe_name = _extract_basename(cmd.split()[0]) if cmd else "process"
        if st_iso:
            try:
                st_dt = datetime.fromisoformat(st_iso.replace("Z", "+00:00"))
                st_ts = st_dt.timestamp()
            except Exception:
                st_ts = time.time()
        else:
            st_ts = time.time()

        events.append({
            "ts": st_ts,
            "text": f"{exe_name} started PID {root_pid or 'unknown'}"
        })
        if task.get("status") in ("completed", "failed", "terminated", "interrupted"):
            dur = float(task.get("duration_seconds") or 0.0)
            code_str = str(task.get("exit_code")) if task.get("exit_code") is not None else "0"
            events.append({
                "ts": st_ts + dur,
                "text": f"{exe_name} exited Code {code_str}"
            })

    # Sort events by timestamp
    events.sort(key=lambda x: x["ts"])

    header = f"PROCESS EVENTS - {task_id}"
    sep = "─" * 56
    lines = [cyan(header), gray(sep)]

    if not events:
        lines.append("  No process events recorded.")
    else:
        for ev in events:
            try:
                local_time = datetime.fromtimestamp(ev["ts"]).strftime("%H:%M:%S")
            except Exception:
                local_time = "00:00:00"
            lines.append(f"{gray(local_time)} {ev['text']}")

    return "\n".join(lines)


def format_resource_consumption(task: Dict[str, Any], tree_data: Optional[Dict[str, Any]]) -> str:
    """Format resource consumption metrics (CPU, RAM, Duration, Processes, I/O)."""
    task_id = task.get("task_id", "")
    header = f"RESOURCE CONSUMPTION - {task_id}"
    sep = "─" * 56
    lines = [cyan(header), gray(sep)]

    cpu_peak = task.get("cpu_peak_percent")
    cpu_avg = task.get("cpu_avg_percent")
    mem_peak = task.get("memory_peak_mb")
    dur_sec = task.get("duration_seconds")

    num_procs = 1
    if tree_data and isinstance(tree_data, dict) and "nodes" in tree_data:
        num_procs = len(tree_data["nodes"])

    out_size_str = "0 B"
    err_size_str = "0 B"
    if task.get("stdout_path") and Path(task["stdout_path"]).exists():
        size = Path(task["stdout_path"]).stat().st_size
        out_size_str = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
    if task.get("stderr_path") and Path(task["stderr_path"]).exists():
        size = Path(task["stderr_path"]).stat().st_size
        err_size_str = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"

    lines.append(f"  {blue('Peak CPU:')}          {f'{cpu_peak:.1f}%' if cpu_peak is not None else 'N/A'}")
    lines.append(f"  {blue('Average CPU:')}       {f'{cpu_avg:.1f}%' if cpu_avg is not None else 'N/A'}")
    lines.append(f"  {blue('Peak Memory:')}       {f'{mem_peak:.1f} MB' if mem_peak is not None else 'N/A'}")
    if dur_sec is not None:
        total_sec = int(round(dur_sec))
        hrs = total_sec // 3600
        mins = (total_sec % 3600) // 60
        secs = total_sec % 60
        dur_fmt = f"{hrs:02d}:{mins:02d}:{secs:02d}"
        lines.append(f"  {blue('Total Duration:')}    {dur_fmt} ({dur_sec:.1f}s)")
    else:
        lines.append(f"  {blue('Total Duration:')}    Running / In progress")
    lines.append(f"  {blue('Processes Tracked:')} {num_procs}")
    lines.append(f"  {blue('Stdout Size:')}       {out_size_str}")
    lines.append(f"  {blue('Stderr Size:')}       {err_size_str}")
    return "\n".join(lines)
