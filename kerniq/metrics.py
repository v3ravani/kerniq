"""Resource metrics tracking (CPU, memory, battery) and threshold evaluation."""

import time
from typing import Any, Dict, Optional, Tuple
import psutil

class ProcessTreeMetrics:
    """Collects CPU and memory metrics for a process and its child tree."""

    @staticmethod
    def get_metrics(pid: int) -> Optional[Dict[str, Any]]:
        """Sample CPU percent and resident memory (RSS) for a process and its children.
        
        Returns None if process is no longer running or inaccessible.
        """
        try:
            parent = psutil.Process(pid)
            if not parent.is_running() or parent.status() == psutil.STATUS_ZOMBIE:
                return None

            processes = [parent]
            try:
                processes.extend(parent.children(recursive=True))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            total_cpu = 0.0
            total_rss_bytes = 0
            child_count = len(processes) - 1

            for p in processes:
                try:
                    if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                        # cpu_percent with interval=None compares against previous call
                        total_cpu += p.cpu_percent(interval=None)
                        mem_info = p.memory_info()
                        total_rss_bytes += mem_info.rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            total_rss_mb = total_rss_bytes / (1024 * 1024)

            return {
                "cpu_percent": round(total_cpu, 1),
                "memory_rss_mb": round(total_rss_mb, 1),
                "child_processes": child_count,
                "timestamp": time.time()
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        except Exception:
            return None

def get_battery_info() -> Dict[str, Any]:
    """Retrieve platform battery state if available, or return unsupported status."""
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return {
                "available": False,
                "percent": None,
                "power_plugged": None,
                "display": "N/A (No battery detected or unsupported)"
            }
        
        status_str = "Plugged in" if battery.power_plugged else "Discharging"
        return {
            "available": True,
            "percent": round(battery.percent, 1),
            "power_plugged": battery.power_plugged,
            "display": f"{battery.percent:.0f}% ({status_str})"
        }
    except Exception:
        return {
            "available": False,
            "percent": None,
            "power_plugged": None,
            "display": "N/A (Metric unavailable)"
        }

class ThresholdEvaluator:
    """Evaluates resource metrics against thresholds with consecutive samples and cooldowns."""

    def __init__(self, rules: Dict[str, Any]):
        self.rules = rules
        self.cpu_samples_above = 0
        self.mem_samples_above = 0
        self.last_cpu_alert_time: Optional[float] = None
        self.last_mem_alert_time: Optional[float] = None

    def evaluate(self, metrics: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Evaluate sample metrics.
        
        Returns (cpu_alert_msg, mem_alert_msg).
        Each string is non-None only when threshold condition is met and cooldown has passed.
        """
        now = time.time()
        cpu_alert = None
        mem_alert = None

        # 1. CPU threshold evaluation
        if self.rules.get("cpu_threshold"):
            thresh = float(self.rules.get("cpu_percent", 80.0))
            req_samples = int(self.rules.get("cpu_duration_samples", 3))
            cooldown = float(self.rules.get("cpu_cooldown_seconds", 300))

            current_cpu = metrics.get("cpu_percent", 0.0)
            if current_cpu >= thresh:
                self.cpu_samples_above += 1
                if self.cpu_samples_above >= req_samples:
                    if self.last_cpu_alert_time is None or (now - self.last_cpu_alert_time) >= cooldown:
                        cpu_alert = (
                            f"Task CPU usage exceeded threshold: {current_cpu:.1f}% "
                            f"(limit: {thresh:.1f}% for {req_samples} samples)"
                        )
                        self.last_cpu_alert_time = now
            else:
                self.cpu_samples_above = 0

        # 2. Memory threshold evaluation
        if self.rules.get("memory_threshold"):
            thresh_mb = float(self.rules.get("memory_mb", 1024.0))
            req_samples = int(self.rules.get("memory_duration_samples", 3))
            cooldown = float(self.rules.get("memory_cooldown_seconds", 300))

            current_mem = metrics.get("memory_rss_mb", 0.0)
            if current_mem >= thresh_mb:
                self.mem_samples_above += 1
                if self.mem_samples_above >= req_samples:
                    if self.last_mem_alert_time is None or (now - self.last_mem_alert_time) >= cooldown:
                        mem_alert = (
                            f"Task Memory usage exceeded threshold: {current_mem:.1f} MB "
                            f"(limit: {thresh_mb:.1f} MB for {req_samples} samples)"
                        )
                        self.last_mem_alert_time = now
            else:
                self.mem_samples_above = 0

        return cpu_alert, mem_alert
