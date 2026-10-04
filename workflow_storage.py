"""Atomic files and isolated weekly generations. No live API calls here."""

from contextlib import contextmanager
import csv
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import fcntl
from datetime import datetime, timezone


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_csv(path, rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, stream.getvalue())


def atomic_write_json(path, value):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_rules(root):
    path = Path(root) / "config/workflow_rules.json"
    return json.loads(path.read_text()) if path.exists() else {}


def run_quality(manifest, rules=None):
    rules = (rules or {}).get("run_quality", {})
    selected = int(manifest.get("selected_categories") or 0)
    successful = int(manifest.get("successful_categories") or 0)
    # Empty, unattempted and failed categories all count as missing coverage.
    missing = max(0, selected - successful)
    share = missing / selected if selected else 1.0
    publishable = 0 < successful <= selected and int(manifest.get("products_examined") or 0) > 0 and share <= rules.get("max_failed_share_to_publish", 0.2)
    return {"failed_category_share": share, "missing_categories": missing,
            "publishable": publishable,
            "quality_status": "passed" if publishable else "partial"}


def current_root(root):
    root = Path(root).resolve()
    pointer = root / ".local/current_generation.json"
    if not pointer.exists():
        return root
    data = json.loads(pointer.read_text())
    target = (root / data["root"]).resolve()
    if not target.is_relative_to(root / ".local/weekly_generations") or not (target / "generation.json").exists():
        raise RuntimeError("Invalid current generation; refusing mixed live outputs")
    return target


@contextmanager
def workflow_lock(root):
    path = Path(root) / ".local/weekly-workflow.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Weekly workflow already running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def prepare_generation(root, run_id):
    root = Path(root).resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", run_id):
        raise ValueError("Invalid run id")
    target = root / ".local/weekly_generations" / run_id
    target.mkdir(parents=True, exist_ok=False)
    source = current_root(root)
    # Independent copies: builders must never follow writable links into live data.
    for name in ("data", "reports", "archive", "web", "research"):
        if (source / name).exists():
            shutil.copytree(source / name, target / name)
    shutil.copytree(root / "config", target / "config")
    # Human research can be newer than the most recent weekly generation.
    for name in ("research",):
        if source != root and (root / name).exists():
            shutil.copytree(root / name, target / name, dirs_exist_ok=True)
    index = root / "archive/product_research_index.csv"
    if source != root and index.exists():
        shutil.copy2(index, target / "archive/product_research_index.csv")
    merge_attempt_history(root, target)
    return target


def merge_attempt_history(root, target):
    """Carry retry bookkeeping across partial runs, never their success dates."""
    state_path = target / "archive/category_scan_state.csv"
    if not state_path.exists():
        return
    with state_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = {r["category_id"]: r for r in reader}
    for name in ("consecutive_failures", "last_attempt_at", "status"):
        if name not in fields:
            fields.append(name)

    def timestamp(value):
        if not value:
            return 0
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()

    for directory in sorted((root / "archive/discovery_runs").glob("*")):
        manifest_path = directory / "run_manifest.json"
        checkpoint = directory / "category_scan_state.csv"
        if not manifest_path.exists() or not checkpoint.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("data_provider") == "mcp_replay" or manifest.get("status") not in {"partial", "failure"}:
            continue
        with checkpoint.open(encoding="utf-8-sig", newline="") as stream:
            for attempted in csv.DictReader(stream):
                key = attempted["category_id"]
                previous = rows.get(key, {"category_id": key, "name": attempted.get("name", ""), "path": attempted.get("path", "")})
                previous_time = max(timestamp(previous.get("last_attempt_at")), timestamp(previous.get("last_scanned_at")))
                if timestamp(attempted.get("last_attempt_at")) <= previous_time:
                    continue
                previous["last_attempt_at"] = attempted["last_attempt_at"]
                previous["consecutive_failures"] = attempted.get("consecutive_failures", 0)
                previous["status"] = "failed" if attempted.get("status") == "failed" else ("scanned" if previous.get("last_scanned_at") else "")
                rows[key] = previous
    atomic_write_csv(state_path, rows.values(), fields)


def publish_generation(root, generation, manifest):
    root, generation = Path(root).resolve(), Path(generation).resolve()
    if not generation.is_relative_to(root / ".local/weekly_generations"):
        raise ValueError("Generation outside staging root")
    if (not run_quality(manifest, load_rules(root))["publishable"]
            or manifest.get("data_provider") == "mcp_replay"
            or manifest.get("status") not in {"success", "success_no_candidates"}
            or manifest.get("run_id") != generation.name):
        raise ValueError("Partial or replay results cannot be published")
    for relative in ("web/index.html", "data/category_shape_validation.csv", "archive/shape_opportunity_library.csv"):
        if not (generation / relative).is_file():
            raise ValueError(f"Missing generation output: {relative}")
    atomic_write_json(generation / "generation.json", manifest)
    # This is the only authoritative commit point for the complete batch.
    atomic_write_json(root / ".local/current_generation.json", {
        "root": str(generation.relative_to(root)), "run_id": manifest["run_id"],
        "published_at": manifest.get("finished_at", ""),
    })
