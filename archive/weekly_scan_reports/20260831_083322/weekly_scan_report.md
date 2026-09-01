# 每周选品扫描汇报

- 状态：failure
- Run ID：`20260831_083322`
- 时间：2026-08-31T21:20:49 -> 2026-08-31T21:20:49
- 数据口径：本次运行，不混用历史输出
- 计划/成功/空返回/失败类目：100/0/0/100
- 本次查看产品数：0
- 合格候选（去重前/后）：0/0
- 进入评分候选：0（覆盖 0 个类目）
- 新增候选入档数：0
- 通过验证进入机会池：0
- 本次验证 Shape opportunity：0
- 人工复核/补数据：0（Watch shape 0，Needs Top100 0）
- 初筛淘汰主要原因：none
- 形态淘汰主要原因：none
- 最新初筛快照：`/Users/y33/项目/Codex/2026-05-06/amz-listing/archive/selection_runs/20260831_083322`
- 最新类目/形态快照：`/Users/y33/项目/Codex/2026-05-06/amz-listing/archive/category_shape_runs/20260831_083322`
- Dashboard：`/Users/y33/项目/Codex/2026-05-06/amz-listing/web/index.html`（mtime 2026-08-31T21:20:36）
- 日志：`/Users/y33/项目/Codex/2026-05-06/amz-listing/logs/weekly_category_scan_20260831_083322.log`

## 失败摘要

- 失败步骤：discover_sorftime_opportunities
- 错误：Sorftime API business error 694: Insufficient request quota

## 健康检查

- critical: discover_sorftime_opportunities failed: Sorftime API business error 694: Insufficient request quota
- critical: Selected categories returned 0 products; this run is invalid and must not replace live outputs
- warning: 1 latest scan day(s) have 0 Shape opportunity rows
