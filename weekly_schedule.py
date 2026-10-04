"""Read-only publication freshness checks for the daily local runner."""
from datetime import datetime, timezone
import json
from workflow_storage import current_root, run_quality


def published_age_days(root, now=None):
    path = current_root(root) / "generation.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text())
    if manifest.get("data_provider") == "mcp_replay" or not run_quality(manifest)["publishable"]:
        return None
    try:
        timestamp = datetime.fromisoformat(manifest.get("finished_at", ""))
        if timestamp.tzinfo is None:
            from zoneinfo import ZoneInfo
            timestamp = timestamp.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return ((now or datetime.now(timezone.utc)) - timestamp).total_seconds() / 86400
    except ValueError:
        return None
