#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
ROOT="${SCRIPT_DIR:h}"
LOG_DIR="$ROOT/logs"
RUN_ID="${AMZ_WEEKLY_RUN_ID:-$(date '+%Y%m%d_%H%M%S')}"
RUN_DATE="${AMZ_WEEKLY_RUN_DATE:-${RUN_ID%%_*}}"
LOG_FILE="$LOG_DIR/weekly_category_scan_${RUN_ID}.log"
LATEST_LOG="$LOG_DIR/weekly_category_scan.latest.log"
LOCK_FILE="$LOG_DIR/weekly_category_scan.lock"
FORCE="${AMZ_WEEKLY_FORCE:-0}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.npm-global/bin:$HOME/.local/bin"
export AMZ_WEEKLY_RUN_ID="$RUN_ID"
export AMZ_WEEKLY_LOG_FILE="$LOG_FILE"

mkdir -p "$LOG_DIR"

for arg in "$@"; do
  case "$arg" in
    --force)
      FORCE=1
      ;;
    *)
      printf 'Unknown argument: %s\n' "$arg" >&2
      exit 2
      ;;
  esac
done

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*" | tee -a "$LOG_FILE"
}

latest_completed_discovery_run_for_date() {
  local day="$1"
  local latest=""
  local run_dir
  for run_dir in "$ROOT"/archive/discovery_runs/${day}_*(N); do
    if discovery_run_succeeded "$run_dir/run_manifest.json"; then
      latest="$run_dir"
    fi
  done
  printf '%s' "$latest"
}

latest_completed_shape_snapshot_for_date() {
  local day="$1"
  local latest=""
  local snapshot
  for snapshot in "$ROOT"/archive/category_shape_runs/${day}_*(N); do
    if [[ -s "$snapshot/category_shape_validation.csv" && -s "$snapshot/latest_category_shape_validation.csv" && -s "$snapshot/category_shape_validation.md" ]]; then
      latest="$snapshot"
    fi
  done
  printf '%s' "$latest"
}

cleanup() {
  local exit_code=$?
  if (( exit_code != 0 )); then
    printf '[%s] Weekly category scan failed with exit code %s.\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$exit_code" >> "$LOG_FILE"
  fi
  cp "$LOG_FILE" "$LATEST_LOG" 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT

csv_count() {
  local file_path="$1"
  if [[ ! -f "$file_path" ]]; then
    printf '0'
    return
  fi
  python3 -c 'import csv, sys; f = open(sys.argv[1], "r", encoding="utf-8-sig", newline=""); print(sum(1 for _ in csv.DictReader(f))); f.close()' "$file_path"
}

csv_count_matching() {
  local file_path="$1"
  local key="$2"
  local value="$3"
  if [[ ! -f "$file_path" ]]; then
    printf '0'
    return
  fi
  python3 -c 'import csv, sys; file_path, key, value = sys.argv[1:4]; f = open(file_path, "r", encoding="utf-8-sig", newline=""); print(sum(1 for row in csv.DictReader(f) if row.get(key) == value)); f.close()' "$file_path" "$key" "$value"
}

csv_unique_count() {
  local file_path="$1"
  local key="$2"
  if [[ ! -f "$file_path" ]]; then
    printf '0'
    return
  fi
  python3 -c 'import csv, sys; f = open(sys.argv[1], "r", encoding="utf-8-sig", newline=""); print(len({row.get(sys.argv[2], "") for row in csv.DictReader(f) if row.get(sys.argv[2], "")})); f.close()' "$file_path" "$key"
}

top_shape_reject_reasons() {
  local file_path="$1"
  if [[ ! -f "$file_path" ]]; then
    printf 'none'
    return
  fi
  python3 -c 'import csv, sys; from collections import Counter; c = Counter(); f = open(sys.argv[1], "r", encoding="utf-8-sig", newline=""); rows = csv.DictReader(f); [c.update([part.strip() for part in (row.get("validation_flags") or "").split(";") if part.strip()]) for row in rows if row.get("shape_recommendation") == "Reject category/form"]; f.close(); print(", ".join(f"{k}:{v}" for k, v in c.most_common(5)) or "none")' "$file_path"
}

discovery_run_succeeded() {
  local manifest_path="$1"
  [[ -s "$manifest_path" ]] || return 1
  python3 -c 'import json, sys; data=json.load(open(sys.argv[1], encoding="utf-8")); ok=data.get("status") in {"success", "success_no_candidates"} and int(data.get("successful_categories") or 0)>0 and int(data.get("products_examined") or 0)>0; raise SystemExit(0 if ok else 1)' "$manifest_path"
}

if ! /usr/bin/shlock -p "$$" -f "$LOCK_FILE"; then
  log "Another weekly category scan is already running; exiting."
  exit 0
fi

cd "$ROOT"

if [[ "$FORCE" != "1" ]]; then
  completed_discovery_run="$(latest_completed_discovery_run_for_date "$RUN_DATE")"
  completed_run_id="${completed_discovery_run:t}"
  discovery_manifest="$ROOT/archive/discovery_runs/$completed_run_id/run_manifest.json"
  matching_shape_snapshot="$ROOT/archive/category_shape_runs/$completed_run_id"
  if [[ -n "$completed_discovery_run" && -d "$matching_shape_snapshot" && -s "$matching_shape_snapshot/category_shape_validation.csv" && -s "$ROOT/web/index.html" ]] && discovery_run_succeeded "$discovery_manifest"; then
    log "Completed weekly category scan already exists for $RUN_DATE; exiting without a duplicate run."
    log "Existing discovery run: $completed_discovery_run"
    log "Existing category/form snapshot: $matching_shape_snapshot"
    log "Existing discovery manifest: $discovery_manifest"
    log "Dashboard: $ROOT/web/index.html"
    log "Use --force or AMZ_WEEKLY_FORCE=1 only after recording the rerun reason."
    exit 0
  fi
else
  log "Force mode enabled; same-day completed snapshot guard is bypassed."
fi

log "Starting weekly category scan."
log "Project: $ROOT"

python3 refresh_selection_workflow.py --no-issue-comment >> "$LOG_FILE" 2>&1

required_outputs=(
  "web/index.html"
  "reports/discovered_categories.csv"
  "archive/category_scan_state.csv"
  "data/category_shape_validation.csv"
  "archive/shape_opportunity_library.csv"
)

missing=()
for output_path in "${required_outputs[@]}"; do
  if [[ ! -s "$ROOT/$output_path" ]]; then
    missing+=("$output_path")
  fi
done

if (( ${#missing[@]} > 0 )); then
  log "Scan finished but required outputs are missing or empty: ${missing[*]}"
  exit 1
fi

latest_shape_snapshot="$(find "$ROOT/archive/category_shape_runs" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort | tail -n 1 || true)"

scanned_categories="$(csv_count "$ROOT/reports/discovered_categories.csv")"
validated_categories="$(csv_unique_count "$ROOT/data/category_shape_validation.csv" "category_path")"
validation_rows="$(csv_count "$ROOT/data/category_shape_validation.csv")"
shape_opportunities="$(csv_count_matching "$ROOT/data/category_shape_validation.csv" "shape_recommendation" "Shape opportunity")"
watch_shapes="$(csv_count_matching "$ROOT/data/category_shape_validation.csv" "shape_recommendation" "Watch shape")"
active_pool_rows="$(csv_count_matching "$ROOT/archive/shape_opportunity_library.csv" "archive_status" "active_in_latest_run")"
reject_reasons="$(top_shape_reject_reasons "$ROOT/data/category_shape_validation.csv")"

log "Scanned categories: $scanned_categories"
log "Validated categories: $validated_categories"
log "Category/form validation rows: $validation_rows"
log "Shape opportunities in latest validation: $shape_opportunities"
log "Watch shapes in latest validation: $watch_shapes"
log "Active validated opportunity pool rows: $active_pool_rows"
log "Top shape rejection reasons: $reject_reasons"
log "Latest category/form snapshot: ${latest_shape_snapshot:-none}"
log "Dashboard updated: $ROOT/web/index.html"
log "Weekly category scan completed successfully."
