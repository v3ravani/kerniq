"""Kerniq export engine.

Supports exporting task details, task history, and activity logs to:
- JSON (structured JSON dump)
- CSV (standard comma-separated values)
- PDF (formatted multi-page document)
"""

import csv
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from kerniq.config import load_config


def get_default_export_format() -> str:
    """Retrieve default export format configured in advance settings (default: json)."""
    try:
        cfg = load_config()
        fmt = cfg.get("advanced", {}).get("export_format", "json")
        if fmt and str(fmt).lower() in ("json", "csv", "pdf"):
            return str(fmt).lower()
    except Exception:
        pass
    return "json"


def _generate_pdf(title: str, sections: List[tuple[str, List[str]]], output_path: Path) -> Path:
    """Generate a clean PDF report with title and sections."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        c = canvas.Canvas(str(output_path), pagesize=letter)
        width, height = letter

        y = height - 50
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, y, title)
        y -= 25

        c.setStrokeColorRGB(0.7, 0.7, 0.7)
        c.line(50, y, width - 50, y)
        y -= 20

        for sec_title, lines in sections:
            if y < 80:
                c.showPage()
                y = height - 50

            if sec_title:
                c.setFont("Helvetica-Bold", 12)
                c.drawString(50, y, sec_title)
                y -= 18

            c.setFont("Helvetica", 9)
            for line in lines:
                if y < 60:
                    c.showPage()
                    y = height - 50
                    c.setFont("Helvetica", 9)

                # Truncate line width to fit page
                safe_line = str(line)[:110]
                c.drawString(55, y, safe_line)
                y -= 13

            y -= 10

        c.save()
        return output_path
    except Exception:
        # Pure Python fallback for PDF 1.4
        return _pure_python_pdf_fallback(title, sections, output_path)


def _pure_python_pdf_fallback(title: str, sections: List[tuple[str, List[str]]], output_path: Path) -> Path:
    """Pure-Python fallback PDF generator without external dependencies."""
    def escape_pdf(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    all_lines = []
    for sec_title, lines in sections:
        if sec_title:
            all_lines.append(f"=== {sec_title} ===")
        for line in lines:
            all_lines.append(line)
        all_lines.append("")

    pages_content = []
    chunk_size = 50
    chunks = [all_lines[i:i + chunk_size] for i in range(0, max(1, len(all_lines)), chunk_size)]

    for page_idx, chunk in enumerate(chunks):
        stream_lines = ["BT"]
        if page_idx == 0:
            stream_lines.append("/F1 14 Tf")
            stream_lines.append("50 780 Td")
            stream_lines.append(f"({escape_pdf(title)}) Tj")
            stream_lines.append("0 -20 Td")
            stream_lines.append("/F2 9 Tf")
        else:
            stream_lines.append("/F2 9 Tf")
            stream_lines.append("50 780 Td")

        first = True
        for line in chunk:
            if not first:
                stream_lines.append("0 -13 Td")
            safe = escape_pdf(str(line)[:110])
            stream_lines.append(f"({safe}) Tj")
            first = False

        stream_lines.append("ET")
        pages_content.append("\n".join(stream_lines).encode("latin-1", "replace"))

    objects = []
    # 1: Catalog
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    # 2: Pages container
    kids_refs = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages_content)))
    objects.append(f"<< /Type /Pages /Kids [{kids_refs}] /Count {len(pages_content)} >>".encode("latin-1"))

    # Add Page and Content objects
    font_ref = 3 + len(pages_content) * 2
    for i, content in enumerate(pages_content):
        page_num = 3 + i * 2
        content_num = page_num + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content_num} 0 R "
            f"/Resources << /Font << /F1 {font_ref} 0 R /F2 {font_ref + 1} 0 R >> >> >>".encode("latin-1")
        )
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n".encode("latin-1") + content + b"\nendstream"
        )

    # Fonts
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Assemble PDF with cross-reference table
    out = [b"%PDF-1.4\n"]
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(sum(len(x) for x in out))
        out.append(f"{i} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n")

    xref_offset = sum(len(x) for x in out)
    out.append(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets:
        out.append(f"{off:010d} 00000 n \n".encode("latin-1"))

    out.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
    )

    with open(output_path, "wb") as f:
        for chunk in out:
            f.write(chunk)

    return output_path


def export_task(
    task: Dict[str, Any],
    fmt: Optional[str] = None,
    tree_data: Optional[Dict[str, Any]] = None,
    output_text: Optional[str] = None,
    target_dir: Optional[Path] = None
) -> Path:
    """Export detailed report of a single task."""
    format_type = (fmt or get_default_export_format()).lower()
    if format_type not in ("json", "csv", "pdf"):
        format_type = "json"

    base_dir = target_dir or Path.cwd()
    base_dir.mkdir(parents=True, exist_ok=True)
    task_id = task.get("task_id", "task")
    dest_path = base_dir / f"kerniq_report_{task_id}.{format_type}"

    # Extract log content if not passed
    if output_text is None:
        stdout_path = task.get("stdout_path")
        stderr_path = task.get("stderr_path")
        out_parts = []
        if stdout_path and Path(stdout_path).exists():
            try:
                out_parts.append(Path(stdout_path).read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
        if stderr_path and Path(stderr_path).exists():
            try:
                err_content = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
                if err_content.strip():
                    out_parts.append(f"--- STDERR ---\n{err_content}")
            except Exception:
                pass
        output_text = "\n".join(out_parts)

    if format_type == "json":
        data = {
            "task_id": task_id,
            "command": task.get("command"),
            "status": task.get("status"),
            "exit_code": task.get("exit_code"),
            "start_time": task.get("start_time"),
            "end_time": task.get("end_time"),
            "duration_seconds": task.get("duration_seconds"),
            "cpu_avg_percent": task.get("cpu_avg_percent"),
            "cpu_peak_percent": task.get("cpu_peak_percent"),
            "memory_peak_mb": task.get("memory_peak_mb"),
            "stdout_path": task.get("stdout_path"),
            "stderr_path": task.get("stderr_path"),
            "process_tree": tree_data or {},
            "output_preview": output_text[:4000] if output_text else ""
        }
        with open(dest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return dest_path

    elif format_type == "csv":
        with open(dest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Field", "Value"])
            writer.writerow(["Task ID", task_id])
            writer.writerow(["Command", task.get("command", "")])
            writer.writerow(["Status", task.get("status", "")])
            writer.writerow(["Exit Code", str(task.get("exit_code", ""))])
            writer.writerow(["Start Time", str(task.get("start_time", ""))])
            writer.writerow(["End Time", str(task.get("end_time", ""))])
            writer.writerow(["Duration (seconds)", str(task.get("duration_seconds", ""))])
            writer.writerow(["Average CPU %", str(task.get("cpu_avg_percent", ""))])
            writer.writerow(["Peak CPU %", str(task.get("cpu_peak_percent", ""))])
            writer.writerow(["Peak Memory (MB)", str(task.get("memory_peak_mb", ""))])
            writer.writerow(["Stdout Log", str(task.get("stdout_path", ""))])
            writer.writerow(["Stderr Log", str(task.get("stderr_path", ""))])
        return dest_path

    elif format_type == "pdf":
        sections = [
            ("Task Summary", [
                f"Task ID:          {task_id}",
                f"Command:          {task.get('command')}",
                f"Status:           {str(task.get('status')).upper()}",
                f"Exit Code:        {task.get('exit_code')}",
                f"Start Time:       {task.get('start_time')}",
                f"End Time:         {task.get('end_time')}",
                f"Duration:         {task.get('duration_seconds')} seconds",
            ]),
            ("Resource Utilization", [
                f"Average CPU:      {task.get('cpu_avg_percent') or 0:.1f}%",
                f"Peak CPU:         {task.get('cpu_peak_percent') or 0:.1f}%",
                f"Peak Memory:      {task.get('memory_peak_mb') or 0:.1f} MB",
            ])
        ]
        if output_text and output_text.strip():
            log_lines = [l for l in output_text.splitlines()[:40]]
            sections.append(("Output Excerpt", log_lines))

        return _generate_pdf(f"Kerniq Task Report - {task_id}", sections, dest_path)

    return dest_path


def export_history(
    tasks: List[Dict[str, Any]],
    fmt: Optional[str] = None,
    target_dir: Optional[Path] = None
) -> Path:
    """Export task history records."""
    format_type = (fmt or get_default_export_format()).lower()
    if format_type not in ("json", "csv", "pdf"):
        format_type = "json"

    base_dir = target_dir or Path.cwd()
    base_dir.mkdir(parents=True, exist_ok=True)
    dest_path = base_dir / f"kerniq_history_export.{format_type}"

    if format_type == "json":
        clean_tasks = []
        for t in tasks:
            clean_tasks.append({
                "task_id": t.get("task_id"),
                "command": t.get("command"),
                "status": t.get("status"),
                "exit_code": t.get("exit_code"),
                "start_time": t.get("start_time"),
                "end_time": t.get("end_time"),
                "duration_seconds": t.get("duration_seconds"),
                "cpu_avg_percent": t.get("cpu_avg_percent"),
                "cpu_peak_percent": t.get("cpu_peak_percent"),
                "memory_peak_mb": t.get("memory_peak_mb")
            })
        with open(dest_path, "w", encoding="utf-8") as f:
            json.dump({"tasks": clean_tasks, "total": len(clean_tasks)}, f, indent=2)
        return dest_path

    elif format_type == "csv":
        with open(dest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Task ID", "Command", "Status", "Exit Code",
                "Start Time", "Duration (s)", "Avg CPU %", "Peak CPU %", "Peak Memory (MB)"
            ])
            for t in tasks:
                writer.writerow([
                    t.get("task_id"),
                    t.get("command"),
                    t.get("status"),
                    t.get("exit_code"),
                    t.get("start_time"),
                    t.get("duration_seconds"),
                    t.get("cpu_avg_percent"),
                    t.get("cpu_peak_percent"),
                    t.get("memory_peak_mb")
                ])
        return dest_path

    elif format_type == "pdf":
        lines = []
        for t in tasks:
            tid = t.get("task_id")
            cmd = t.get("command", "")[:45]
            st = t.get("status", "").upper()
            code = t.get("exit_code")
            dur = t.get("duration_seconds")
            lines.append(f"{tid} | {st} (code {code}) | {dur}s | {cmd}")

        sections = [("Recorded Tasks", lines)]
        return _generate_pdf("Kerniq Execution History", sections, dest_path)

    return dest_path


def export_logs(
    logs: List[Dict[str, Any]],
    fmt: Optional[str] = None,
    target_dir: Optional[Path] = None
) -> Path:
    """Export Kerniq activity logs."""
    format_type = (fmt or get_default_export_format()).lower()
    if format_type not in ("json", "csv", "pdf"):
        format_type = "json"

    base_dir = target_dir or Path.cwd()
    base_dir.mkdir(parents=True, exist_ok=True)
    dest_path = base_dir / f"kerniq_logs_export.{format_type}"

    if format_type == "json":
        with open(dest_path, "w", encoding="utf-8") as f:
            json.dump({"logs": logs, "total": len(logs)}, f, indent=2)
        return dest_path

    elif format_type == "csv":
        with open(dest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Action", "Details"])
            for entry in logs:
                writer.writerow([
                    entry.get("timestamp", ""),
                    entry.get("action", ""),
                    entry.get("details", "")
                ])
        return dest_path

    elif format_type == "pdf":
        lines = []
        for entry in logs:
            ts = (entry.get("timestamp") or "")[:19].replace("T", " ")
            act = entry.get("action", "")
            det = entry.get("details", "")
            lines.append(f"[{ts}] {act}: {det}")

        sections = [("Activity Entries", lines)]
        return _generate_pdf("Kerniq System Activity Logs", sections, dest_path)

    return dest_path
