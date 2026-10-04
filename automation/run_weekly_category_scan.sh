#!/bin/zsh
set -euo pipefail
SCRIPT_DIR="${0:A:h}"
ROOT="${SCRIPT_DIR:h}"
export TZ=Asia/Shanghai
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.npm-global/bin:$HOME/.local/bin"
export AMZ_WEEKLY_RUN_ID="${AMZ_WEEKLY_RUN_ID:-$(date '+%Y%m%d_%H%M%S')}"
export AMZ_WEEKLY_LOG_FILE="$ROOT/logs/weekly_category_scan_${AMZ_WEEKLY_RUN_ID}.log"
mkdir -p "$ROOT/logs"
args=(--scheduled)
for arg in "$@"; do
  case "$arg" in
    --force) args=() ;;
    *) printf 'Unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done
if [[ "${AMZ_WEEKLY_FORCE:-0}" == 1 ]]; then
  args=()
fi
finish() {
  local code=$?
  printf '[%s] Workflow exit: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$code" >> "$AMZ_WEEKLY_LOG_FILE"
  cp "$AMZ_WEEKLY_LOG_FILE" "$ROOT/logs/weekly_category_scan.latest.log"
}
trap finish EXIT
cd "$ROOT"
# Python owns the OS advisory lock; crashes cannot leave a stale PID lock.
/usr/bin/caffeinate -i python3 refresh_selection_workflow.py --no-issue-comment "${args[@]}" >> "$AMZ_WEEKLY_LOG_FILE" 2>&1
