"""Unit tests for process metrics, battery status, and threshold evaluation."""

import os
from kerniq.metrics import ProcessTreeMetrics, ThresholdEvaluator, get_battery_info

def test_current_process_metrics():
    pid = os.getpid()
    metrics = ProcessTreeMetrics.get_metrics(pid)

    assert metrics is not None
    assert "cpu_percent" in metrics
    assert "memory_rss_mb" in metrics
    assert metrics["memory_rss_mb"] > 0

def test_nonexistent_process_metrics():
    # Dead PID
    metrics = ProcessTreeMetrics.get_metrics(99999999)
    assert metrics is None

def test_battery_status_structure():
    bat = get_battery_info()
    assert isinstance(bat, dict)
    assert "available" in bat
    assert "display" in bat

def test_threshold_evaluation_consecutive_and_cooldown():
    rules = {
        "cpu_threshold": True,
        "cpu_percent": 50.0,
        "cpu_duration_samples": 2,
        "cpu_cooldown_seconds": 60,
        "memory_threshold": True,
        "memory_mb": 100.0,
        "memory_duration_samples": 2,
        "memory_cooldown_seconds": 60,
    }
    evaluator = ThresholdEvaluator(rules)

    # Sample 1: above threshold, but only 1 sample (requires 2)
    sample1 = {"cpu_percent": 60.0, "memory_rss_mb": 150.0}
    c_alert, m_alert = evaluator.evaluate(sample1)
    assert c_alert is None
    assert m_alert is None

    # Sample 2: above threshold for 2 consecutive samples -> alerts trigger
    sample2 = {"cpu_percent": 70.0, "memory_rss_mb": 160.0}
    c_alert, m_alert = evaluator.evaluate(sample2)
    assert c_alert is not None
    assert "CPU usage exceeded threshold" in c_alert
    assert m_alert is not None
    assert "Memory usage exceeded threshold" in m_alert

    # Sample 3: immediately after -> cooldown suppresses repeated alert
    sample3 = {"cpu_percent": 75.0, "memory_rss_mb": 170.0}
    c_alert, m_alert = evaluator.evaluate(sample3)
    assert c_alert is None
    assert m_alert is None
