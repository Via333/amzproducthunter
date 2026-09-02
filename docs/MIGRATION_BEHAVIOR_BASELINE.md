# AMZ 选品迁移行为基线

本基线用于把迁移验收从 `py_compile` 提升为可对账的行为验收。后续迁移、重构或规则调整，必须能解释下列表格中任何变化；未在独立 Issue 中说明并审核的变化，视为回归风险。

## 对照口径

| 项目 | 迁移前既有行为 | 迁移后验收口径 |
| --- | --- | --- |
| 周扫入口 | `python3 refresh_selection_workflow.py` | 入口保持不变；执行 `discover_sorftime_opportunities.py --strategy category`、`category_shape_validation.py`、`build_product_research_pages.py`、`build_dashboard.py` |
| 本机定时入口 | `automation/run_weekly_category_scan.sh` | 入口保持不变；同日完整快照存在时默认不重复调用 Sorftime，`--force` 必须先在 Issue 说明 |
| 单品研究入口 | `python3 refresh_product_research.py --asin ASIN` | 入口保持不变；只登记本地既有研究时使用 `--register-existing` |
| Sorftime 数据边界 | 通过 Sorftime CLI / 现有脚本获取数据 | 不直接爬 Amazon 前台；离线验证使用 fixture / report-only / smoke test，不消耗 Sorftime 额度 |
| 初筛产出 | `reports/selection_ranked.csv`、`reports/selection_report.md` | 手动 ASIN/关键词排序工具保留；周扫不再产出或依赖它们 |
| 类目/形态验证产出 | `data/category_shape_validation.csv`、`reports/category_shape_validation.md` | 路径保持；只有 `shape_recommendation=Shape opportunity` 可进入形态机会池 |
| 网页报告 | `web/index.html` | `python3 build_dashboard.py` 生成同一路径；页面读取当前 reports/data/archive live 文件 |
| 周扫汇报 | 无或依赖日志人工判断 | `reports/weekly_scan_report.json`、`reports/weekly_scan_report.md`、`archive/weekly_scan_reports/RUN_ID/` 可对账 |
| 初筛归档 | `archive/selection_runs/YYYYMMDD_HHMMSS/` | 仅手动评分时生成；不属于周扫验收产出 |
| 初筛机会档案 | `archive/opportunity_library.csv` | legacy 手动候选档案；不再由周扫更新，不等于正式形态机会池 |
| 形态验证归档 | `archive/category_shape_runs/YYYYMMDD_HHMMSS/` | 每次验证快照保留 `category_shape_validation.csv`、`latest_category_shape_validation.csv`、`category_shape_validation.md`、`source_selection_ranked.csv` |
| 形态机会池 | `archive/shape_opportunity_library.csv` | 只收通过类目/形态验证的 Shape opportunity；相同类目/形态用 `shape_archive_key=shape:{category}|{form}` 归并 |
| 单品研究归档 | `archive/product_research_runs/ASIN/YYYYMMDD_HHMMSS/`、`archive/product_research_index.csv` | 保留每次研究报告和 research files；索引更新最近研究时间和次数，不删除旧研究 |
| 日志 | `logs/weekly_category_scan_YYYYMMDD_HHMMSS.log`、`logs/weekly_category_scan.latest.log` | 时间戳日志作为历史；`latest` 只是便利指针，可被下一次运行覆盖 |

## 关键字段基线

`reports/selection_ranked.csv` 至少应保留以下对账字段：

- 来源与商品：`source_strategy`、`source_asin`、`source_parent_asin`、`product_name`、`category`、`listing_url`
- 价格利润：`target_price`、`cost`、`shipping`、`fba_fee`、`referral_fee_rate`、`gross_profit_per_unit`、`gross_margin`、`monthly_gross_profit`
- 需求竞争：`est_monthly_sales`、`avg_review_count`、`avg_rating`、`top10_review_share`、`keyword_search_volume`、`keyword_cpc`
- 风险与推荐：`seasonality_score`、`differentiation_score`、`compliance_risk`、`fragile_risk`、`oversize_risk`、`opportunity_score`、`recommendation`、`key_flags`、`hard_stop_reason`
- 初筛档案：`archive_key`、`archive_first_seen`、`archive_last_seen`、`archive_seen_count`、`archive_best_score`、`archive_latest_score`、`archive_status`、`archive_last_run_id`、`research_status`

`data/category_shape_validation.csv` 至少应保留以下对账字段：

- 种子：`seed_asin`、`seed_listing_url`、`seed_title`、`seed_score`、`seed_recommendation`
- 类目：`category_path`、`data_quality`、`category_sample_count`、`category_total_sales`、`category_median_reviews`、`category_top10_median_reviews`、`category_top_brand`、`category_top_brand_share`
- 形态：`product_form`、`shape_scope`（`seed_form` / `adjacent_form` / `category_form`）、`shape_score`、`shape_recommendation`、`form_count`、`form_avg_price`、`form_avg_sales`、`form_median_reviews`、`form_low_review_high_sales_count`、`form_new_entrant_count`、`form_new_entrant_success_count`、`form_new_entrant_success_rate`、`form_brand_dependent_share`、`form_reference_asins`
- 种子聚合：`seed_asins`、`seed_count`（同一最小类目的所有种子）
- 结论：`validation_flags`、`opportunity_thesis`、`next_action`、`research_page`

`archive/shape_opportunity_library.csv` 应额外保留：

- `shape_archive_key`、`archive_first_seen`、`archive_last_seen`、`archive_seen_count`、`archive_best_score`、`archive_latest_score`、`archive_status`、`archive_last_run_id`、`research_status`、`archive_notes`

## 当前规则阈值基线

初筛评分来自 `config/scoring_rules.json`：

- 总分权重：需求 0.15、竞争 0.30、利润 0.35、差异化 0.10、风险控制 0.10。
- Go 阈值：`opportunity_score >= 70`、毛利率 `>= 35%`、单件毛利 `>= $8`、合规风险 `<= 40`。
- Watch 阈值：`opportunity_score >= 52`、毛利率 `>= 30%`、单件毛利 `>= $8`、平均评论数 `<= 600`、大件风险 `<= 60`。
- 硬停止：合规风险 `>= 80`、大件风险 `>= 65`、易碎风险 `>= 80`、季节性风险 `>= 80`；硬停止分数上限 35。
- 淘汰分数上限：无硬停止但最终 Reject 时，分数上限 45。
- 默认佣金率：15%。

大件/物流风险来自 `import_sorftime_candidates.py`：

- 标题/类目/品牌命中 air mover、steam cleaner、vacuum、solar lights、electric bike pump、desk、rug、deck box、laundry basket、pillow 等硬大件词时，大件风险至少 85。
- 命中 garage lights、with hose、16ft hose、accessories、multi-purpose cleaner 等软大件词时，大件风险至少 65。
- 重量 `>= 5000` 时至少 85，`>= 2000` 时至少 65，`>= 1000` 时至少 45。
- 尺寸列表中最大边 `>= 45` 时至少 60。

类目/形态验证来自 `config/category_shape_validation_rules.json`：

- 周扫参数 `seed_limit=0`、`category_limit=0`、`category_ranking_limit=0`：不读初筛种子，对本轮所有成功扫描且有原始 Top100 的类目做形态验证。
- 类目评论墙：类目评论中位数 `>= 1000`，Top10 评论中位数 `>= 1500`。
- 品牌集中：Top 品牌份额 `>= 35%`。
- 形态样本：`form_count >= 2`；相邻/类目形态入池需要 `>= 3`。
- 形态需求：形态月销均值 `>= 150` 或形态总月销 `>= 800`。
- 形态评论：形态评论中位数 `<= 300`；若超过 300，则必须有至少 2 个低评高销样本。
- 新进入者：`online_date` 在 18 个月内；成功 = 月销 `>= 200`；至少 2 个新进入者才算完整证据；参考 ASIN 取评论 `<= 300` 的前 5 个。
- 品牌依赖：形态内 `>= 50%` listing 为其他品牌的兼容件/替换件时必淘汰。
- 形态机会：`shape_score >= 65`，无硬 flag（种子形态或样本 `>= 3` 的相邻/类目形态）。
- 观察形态：`shape_score >= 55`。

## 行为验收命令

不调用 Sorftime 的本地 smoke test：

```bash
python3 tests/smoke_selection_workflow.py
```

预期结果：验证 2 个成功类目、3 个形态；`Shape opportunity=1`、`Watch shape=1`、`Reject category/form=1`；参考 ASIN 非空；临时形态机会池 `active_in_latest_run=1`。

不调用 Sorftime 的 report-only 验证：

```bash
python3 refresh_selection_workflow.py --report-only --no-issue-comment
```

该命令只读取当前 live 文件，重写周扫汇报和周扫汇报归档，不更新候选、验证或机会池。
