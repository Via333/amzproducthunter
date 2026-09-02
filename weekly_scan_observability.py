#!/usr/bin/env python3
"""Build weekly scan metrics, health checks, and optional Multica issue comments."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


REPORT_JSON = "weekly_scan_report.json"
REPORT_MD = "weekly_scan_report.md"

SECRET_PATTERNS = [
    re.compile(r"(account[-_\s]*sk\s*[:=]\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[-_\s]*key\s*[:=]\s*)\S+", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)\S+", re.IGNORECASE),
    re.compile(r"(cookie\s*[:=]\s*)\S+", re.IGNORECASE),
    re.compile(r"(bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sanitize(value: Any) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.groups() else "[REDACTED]", text)
    return text


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def csv_count(path: Path) -> int:
    return len(read_csv_rows(path))


def count_matching(path: Path, key: str, value: str) -> int:
    return sum(1 for row in read_csv_rows(path) if row.get(key) == value)


def count_in(path: Path, key: str, values: set[str]) -> int:
    return sum(1 for row in read_csv_rows(path) if row.get(key) in values)


def latest_child_dir(path: Path) -> Path | None:
    if not path.exists():
        return None
    children = [child for child in path.iterdir() if child.is_dir()]
    return sorted(children)[-1] if children else None


def file_mtime(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def file_age_days(path: Path) -> float | None:
    if not path.exists():
        return None
    age_seconds = datetime.now().timestamp() - path.stat().st_mtime
    return round(max(0.0, age_seconds / 86400), 2)


# Flags that describe the evidence state but never cause a rejection. Counting
# them as "reject reasons" made the weekly report claim that "supplier quote
# required" was the top reason 182 candidates were rejected.
INFORMATIONAL_FLAGS = (
    "supplier quote required",
    "estimated profit is not a rejection gate",
    "clean early risk profile",
    "no listing-age data",
)


def is_informational_flag(flag: str) -> bool:
    lowered = flag.lower()
    return any(marker in lowered for marker in INFORMATIONAL_FLAGS)


def seed_reject_reason(row: dict[str, str]) -> str:
    """Single best explanation for why an initial-screen candidate was rejected."""

    hard_stop = str(row.get("hard_stop_reason") or "").strip()
    if hard_stop:
        return hard_stop
    moat = str(row.get("brand_moat_reason") or "").strip()
    if moat:
        return moat
    gating = [
        part.strip()
        for part in str(row.get("key_flags") or "").split(";")
        if part.strip() and not is_informational_flag(part)
    ]
    # Flags that map to an explicit Watch gate in product_selection.recommend().
    for flag in gating:
        if flag.startswith("review count above") or flag.startswith("oversize risk"):
            return flag
        if flag.startswith("evidence confidence"):
            return "evidence confidence below watch threshold"
    return "score below watch threshold"


def top_reject_reasons(path: Path, recommendation_key: str, reject_value: str, flags_key: str) -> str:
    counter: Counter[str] = Counter()
    for row in read_csv_rows(path):
        if row.get(recommendation_key) != reject_value:
            continue
        if flags_key == "key_flags":
            counter[seed_reject_reason(row)] += 1
            continue
        flags = str(row.get(flags_key) or "")
        counter.update(part.strip() for part in flags.split(";") if part.strip() and not is_informational_flag(part))
    return ", ".join(f"{reason}:{count}" for reason, count in counter.most_common(5)) or "none"


def top_categories_by_health(path: Path, limit: int = 5) -> str:
    rows = [row for row in read_csv_rows(path) if row.get("category_health_score")]
    if not rows:
        return "none"
    rows.sort(key=lambda row: -to_float_safe(row.get("category_health_score")))
    return "; ".join(
        f"{row.get('name') or row.get('path')}({row.get('category_health_score')})" for row in rows[:limit]
    )


def to_float_safe(value: object) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except ValueError:
        return 0.0


def latest_selection_run_id(root: Path) -> str:
    latest = latest_child_dir(root / "archive" / "selection_runs")
    if not latest:
        return ""
    meta_path = latest / "run_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return str(meta.get("run_id") or latest.name)
        except json.JSONDecodeError:
            return latest.name
    return latest.name


def latest_success_log(root: Path) -> Path | None:
    candidates = []
    for path in (root / "logs").glob("weekly_category_scan*.log"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Weekly category scan completed successfully." in text:
            candidates.append(path)
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def latest_daily_shape_counts(root: Path) -> list[dict[str, Any]]:
    runs_root = root / "archive" / "category_shape_runs"
    by_day: dict[str, Path] = {}
    if runs_root.exists():
        for run_dir in sorted(child for child in runs_root.iterdir() if child.is_dir()):
            day = run_dir.name.split("_", 1)[0]
            by_day[day] = run_dir
    daily = []
    for day, run_dir in sorted(by_day.items()):
        validation_path = run_dir / "category_shape_validation.csv"
        discovery_manifest = root / "archive" / "discovery_runs" / run_dir.name / "run_manifest.json"
        if discovery_manifest.exists():
            try:
                discovery_status = json.loads(discovery_manifest.read_text(encoding="utf-8")).get("status")
            except json.JSONDecodeError:
                discovery_status = "failure"
            if discovery_status not in {"success", "success_no_candidates"}:
                continue
        daily.append(
            {
                "day": day,
                "run_id": run_dir.name,
                "shape_opportunities": count_matching(validation_path, "shape_recommendation", "Shape opportunity"),
            }
        )
    return daily


def consecutive_zero_shape_days(root: Path, threshold: int) -> tuple[int, list[dict[str, Any]]]:
    daily = latest_daily_shape_counts(root)
    streak = 0
    for item in reversed(daily):
        if item["shape_opportunities"] == 0:
            streak += 1
        else:
            break
    return streak, daily[-threshold:]


def health_checks(
    root: Path,
    zero_pool_weeks: int,
    stale_days: int,
    log_path: Path | None,
    current_status: str = "",
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    dashboard = root / "web" / "index.html"
    dashboard_age = file_age_days(dashboard)
    if dashboard_age is None:
        checks.append({"name": "dashboard_exists", "status": "critical", "message": "web/index.html is missing"})
    elif dashboard_age > stale_days:
        checks.append(
            {
                "name": "dashboard_freshness",
                "status": "critical",
                "message": f"web/index.html is {dashboard_age} days old, above {stale_days} day threshold",
            }
        )
    else:
        checks.append(
            {
                "name": "dashboard_freshness",
                "status": "ok",
                "message": f"web/index.html age is {dashboard_age} days",
            }
        )

    zero_streak, recent_days = consecutive_zero_shape_days(root, zero_pool_weeks)
    if zero_streak >= zero_pool_weeks:
        checks.append(
            {
                "name": "consecutive_zero_pool",
                "status": "critical",
                "message": f"{zero_streak} latest scan days have 0 Shape opportunity rows",
            }
        )
    elif zero_streak:
        checks.append(
            {
                "name": "consecutive_zero_pool",
                "status": "warning",
                "message": f"{zero_streak} latest scan day(s) have 0 Shape opportunity rows",
            }
        )
    else:
        checks.append(
            {
                "name": "consecutive_zero_pool",
                "status": "ok",
                "message": f"Recent shape opportunity counts: {recent_days}",
            }
        )

    success_log = latest_success_log(root)
    success_age = file_age_days(success_log) if success_log else None
    if current_status == "success":
        checks.append(
            {
                "name": "weekly_success_log",
                "status": "ok",
                "message": "Current workflow reached success; launch wrapper appends the completion marker after report writing",
            }
        )
    elif success_log is None:
        checks.append(
            {
                "name": "weekly_success_log",
                "status": "critical",
                "message": "No weekly_category_scan*.log contains a successful completion marker",
            }
        )
    elif success_age is not None and success_age > stale_days:
        checks.append(
            {
                "name": "weekly_success_log",
                "status": "critical",
                "message": f"Latest successful weekly log is {success_age} days old: {success_log}",
            }
        )
    else:
        checks.append(
            {
                "name": "weekly_success_log",
                "status": "ok",
                "message": f"Latest successful weekly log: {success_log}",
            }
        )

    if log_path:
        if log_path.exists():
            checks.append({"name": "current_log", "status": "ok", "message": f"Current log path: {log_path}"})
        else:
            checks.append({"name": "current_log", "status": "warning", "message": f"Current log path not found yet: {log_path}"})

    return checks


def collect_metrics(root: Path, run_id: str) -> dict[str, Any]:
    discovery_dir = root / "archive" / "discovery_runs" / run_id
    shape_dir = root / "archive" / "category_shape_runs" / run_id
    discovery_manifest_path = discovery_dir / "run_manifest.json"
    validation_path = shape_dir / "category_shape_validation.csv"
    shape_library_path = root / "archive" / "shape_opportunity_library.csv"
    discovery_manifest = {}
    if discovery_manifest_path.exists():
        try:
            discovery_manifest = json.loads(discovery_manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            discovery_manifest = {}

    validation_rows = read_csv_rows(validation_path)
    shape_library_rows = read_csv_rows(shape_library_path)
    new_pool_shapes = sum(
        1
        for row in shape_library_rows
        if row.get("archive_last_run_id") == run_id
        and row.get("archive_status") == "active_in_latest_run"
        and str(row.get("archive_seen_count") or "") == "1"
    )
    shape_opportunity_rows = [row for row in validation_rows if row.get("shape_recommendation") == "Shape opportunity"]
    reference_asin_count = sum(
        len([part for part in str(row.get("form_reference_asins", "") or "").split(";") if part.strip()])
        for row in shape_opportunity_rows
    )
    validated_categories = len({row.get("category_path", "") for row in validation_rows if row.get("category_path")})
    active_pool_rows = sum(1 for row in shape_library_rows if row.get("archive_status") == "active_in_latest_run")

    return {
        "metrics_source": "current_run",
        "discovery_status": discovery_manifest.get("status", "not_started"),
        "selected_categories": int(discovery_manifest.get("selected_categories") or 0),
        "scanned_categories": int(discovery_manifest.get("successful_categories") or 0),
        "empty_categories": int(discovery_manifest.get("empty_categories") or 0),
        "failed_categories": int(discovery_manifest.get("failed_categories") or 0),
        "products_examined": int(discovery_manifest.get("products_examined") or 0),
        "validated_categories": validated_categories,
        "validation_rows": len(validation_rows),
        "shape_opportunities_latest_validation": len(shape_opportunity_rows),
        "watch_shapes": count_matching(validation_path, "shape_recommendation", "Watch shape"),
        "rejected_shapes": count_matching(validation_path, "shape_recommendation", "Reject category/form"),
        "needs_category_top100": count_matching(validation_path, "shape_recommendation", "Needs category Top100"),
        "manual_review_rows": count_in(
            validation_path,
            "shape_recommendation",
            {"Watch shape", "Needs category Top100"},
        ),
        "new_pool_shapes": new_pool_shapes,
        "reference_asin_count": reference_asin_count,
        "active_validated_pool_rows": active_pool_rows,
        "total_validated_pool_rows": len(shape_library_rows),
        "top_categories_by_health": top_categories_by_health(root / "reports" / "discovered_categories.csv"),
        "shape_reject_reasons": top_reject_reasons(
            validation_path,
            "shape_recommendation",
            "Reject category/form",
            "validation_flags",
        ),
    }


def build_report(
    *,
    root: Path,
    run_id: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    log_path: Path | None = None,
    failed_step: str = "",
    error_summary: str = "",
    zero_pool_weeks: int = 3,
    stale_days: int = 8,
) -> dict[str, Any]:
    current_shape = root / "archive" / "category_shape_runs" / run_id
    current_discovery = root / "archive" / "discovery_runs" / run_id
    latest_shape = current_shape if current_shape.exists() else None
    finished_at = finished_at or now_iso()
    metrics = collect_metrics(root, run_id)
    checks = health_checks(root, zero_pool_weeks, stale_days, log_path, status)
    if metrics["selected_categories"] > 0 and metrics["products_examined"] == 0:
        checks.insert(
            0,
            {
                "name": "zero_products_examined",
                "status": "critical",
                "message": "Selected categories returned 0 products; this run is invalid and must not replace live outputs",
            },
        )
    if status == "failure":
        checks.insert(
            0,
            {
                "name": "workflow_failure",
                "status": "critical",
                "message": f"{failed_step or 'weekly workflow'} failed: {sanitize(error_summary)[:500]}",
            },
        )

    return {
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "failed_step": failed_step,
        "error_summary": sanitize(error_summary)[:1000],
        "metrics": metrics,
        "paths": {
            "current_discovery_run": str(current_discovery if current_discovery.exists() else ""),
            "latest_category_shape_snapshot": str(latest_shape or ""),
            "dashboard": str(root / "web" / "index.html"),
            "dashboard_mtime": file_mtime(root / "web" / "index.html"),
            "log_path": str(log_path or ""),
            "latest_report_json": str(root / "reports" / REPORT_JSON),
            "latest_report_md": str(root / "reports" / REPORT_MD),
        },
        "health_checks": checks,
    }


def report_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    paths = report["paths"]
    alert_checks = [check for check in report["health_checks"] if check["status"] != "ok"]
    lines = [
        "# 每周选品扫描汇报",
        "",
        f"- 状态：{report['status']}",
        f"- Run ID：`{report['run_id']}`",
        f"- 时间：{report['started_at']} -> {report['finished_at']}",
        f"- 数据口径：本次运行，不混用历史输出",
        f"- 计划/成功/空返回/失败类目：{metrics['selected_categories']}/{metrics['scanned_categories']}/{metrics['empty_categories']}/{metrics['failed_categories']}",
        f"- 本次查看产品数：{metrics['products_examined']}",
        f"- 验证类目数：{metrics['validated_categories']}",
        f"- 形态数：{metrics['validation_rows']}（Shape opportunity {metrics['shape_opportunities_latest_validation']} / Watch {metrics['watch_shapes']} / Reject {metrics['rejected_shapes']}）",
        f"- 新增入池形态：{metrics['new_pool_shapes']}，当前有效机会池：{metrics['active_validated_pool_rows']}",
        f"- 参考 ASIN 总数：{metrics['reference_asin_count']}",
        f"- 人工复核/补数据：{metrics['manual_review_rows']}（Watch shape {metrics['watch_shapes']}，Needs Top100 {metrics['needs_category_top100']}）",
        f"- 类目健康度 Top5：{metrics.get('top_categories_by_health', 'none')}",
        f"- 形态淘汰主要原因：{metrics['shape_reject_reasons']}",
        f"- 最新类目/形态快照：`{paths['latest_category_shape_snapshot'] or 'none'}`",
        f"- Dashboard：`{paths['dashboard']}`（mtime {paths['dashboard_mtime'] or 'missing'}）",
        f"- 日志：`{paths['log_path'] or 'none'}`",
    ]
    if report.get("failed_step"):
        lines.extend(["", "## 失败摘要", "", f"- 失败步骤：{report['failed_step']}", f"- 错误：{report.get('error_summary') or 'unknown'}"])
    lines.extend(["", "## 健康检查", ""])
    if alert_checks:
        for check in alert_checks:
            lines.append(f"- {check['status']}: {check['message']}")
    else:
        lines.append("- ok: 未发现需要告警的健康检查项")
    return "\n".join(lines).strip() + "\n"


def issue_comment(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    alerts = [check for check in report["health_checks"] if check["status"] != "ok"]
    lines = [
        f"每周选品扫描：{report['status']}（Run `{report['run_id']}`）",
        "",
        f"- 计划/成功/失败类目：{metrics['selected_categories']}/{metrics['scanned_categories']}/{metrics['failed_categories']}",
        f"- 验证类目/形态：{metrics['validated_categories']}/{metrics['validation_rows']}",
        f"- 入机会池：{metrics['active_validated_pool_rows']}（本次新增 {metrics['new_pool_shapes']}）",
        f"- Shape opportunity / Watch / Reject：{metrics['shape_opportunities_latest_validation']}/{metrics['watch_shapes']}/{metrics['rejected_shapes']}",
        f"- 参考 ASIN：{metrics['reference_asin_count']}",
        f"- 人工复核/补数据：{metrics['manual_review_rows']}",
        f"- 形态淘汰主因：{metrics['shape_reject_reasons']}",
        f"- Dashboard mtime：{report['paths']['dashboard_mtime'] or 'missing'}",
        f"- 最新快照：{report['paths']['latest_category_shape_snapshot'] or 'none'}",
        f"- 日志：{report['paths']['log_path'] or 'none'}",
    ]
    if report.get("failed_step"):
        lines.extend(["", f"失败步骤：{report['failed_step']}", f"错误摘要：{report.get('error_summary') or 'unknown'}"])
    lines.append("")
    if alerts:
        lines.append("健康检查告警：")
        lines.extend(f"- {check['status']}: {check['message']}" for check in alerts)
    else:
        lines.append("健康检查：未发现告警。")
    return "\n".join(lines).strip() + "\n"


def write_report_files(root: Path, report: dict[str, Any]) -> tuple[Path, Path, Path]:
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / REPORT_JSON
    md_path = reports_dir / REPORT_MD
    markdown = report_markdown(report)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")

    archive_dir = root / "archive" / "weekly_scan_reports" / str(report["run_id"])
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_json = archive_dir / REPORT_JSON
    archive_md = archive_dir / REPORT_MD
    shutil.copyfile(json_path, archive_json)
    shutil.copyfile(md_path, archive_md)
    return json_path, md_path, archive_dir


def post_issue_comment(issue_id: str, content: str) -> None:
    command = ["multica", "issue", "comment", "add", issue_id, "--content-stdin"]
    completed = subprocess.run(command, input=content, text=True, capture_output=True)
    if completed.returncode != 0:
        stderr = sanitize(completed.stderr.strip() or completed.stdout.strip())
        raise RuntimeError(f"multica issue comment add failed: {stderr}")


def maybe_post_issue_comment(issue_id: str, report: dict[str, Any]) -> bool:
    issue_id = str(issue_id or "").strip()
    if not issue_id:
        return False
    try:
        post_issue_comment(issue_id, issue_comment(report))
        return True
    except Exception as exc:  # noqa: BLE001 - reporting must not hide the scan result.
        print(f"Warning: {sanitize(exc)}", file=sys.stderr)
        return False


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
