# 每周选品扫描汇报

- 状态：failure
- Run ID：`20260810_084304`
- 时间：2026-08-10T08:43:04 -> 2026-08-10T10:16:36
- 扫描类目数：100
- 候选产品数：200
- 新增候选入档数：2
- 通过验证进入机会池：0
- 本次验证 Shape opportunity：0
- 人工复核/补数据：4（Watch shape 0，Needs Top100 4）
- 初筛淘汰主要原因：gross margin below 35%:195, unit profit below $8:159, review count above 600:56, hard oversize/logistics risk:55, oversize risk:55
- 形态淘汰主要原因：none
- 最新初筛快照：`/Users/y33/项目/Codex/2026-05-06/amz-listing/archive/selection_runs/20260809_173908`
- 最新类目/形态快照：`/Users/y33/项目/Codex/2026-05-06/amz-listing/archive/category_shape_runs/20260809_173908`
- Dashboard：`/Users/y33/项目/Codex/2026-05-06/amz-listing/web/index.html`（mtime 2026-08-10T07:36:21）
- 日志：`/Users/y33/项目/Codex/2026-05-06/amz-listing/logs/weekly_category_scan_20260810_084304.log`

## 失败摘要

- 失败步骤：discover_sorftime_opportunities
- 错误：discover_sorftime_opportunities failed with exit code 1: python3 discover_sorftime_opportunities.py --strategy category --score

## 健康检查

- critical: discover_sorftime_opportunities failed: discover_sorftime_opportunities failed with exit code 1: python3 discover_sorftime_opportunities.py --strategy category --score
- warning: 2 latest scan day(s) have 0 Shape opportunity rows
- critical: No weekly_category_scan*.log contains a successful completion marker
