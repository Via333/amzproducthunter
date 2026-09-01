# AMZ 选品自动化工作流

本文档是项目执行 Runbook，供本地执行智能体和人工维护者使用。

## 角色分工

- 执行智能体：运行脚本、更新机会池、排查自动化失败、生成网页报告、维护归档。
- 项目审核智能体：复核重要规则调整、迁移方案和高影响变更。
- 人工负责人：决定是否打样、询价、采购或进一步投入供应链资源。

执行智能体完成重要修改后，把 Issue 设为待审核/`in_review`，不要直接视为最终通过。

## 不可破坏规则

- 不删除文件、目录或历史归档。
- 不批量清理 `archive/`、`reports/`、`research/`、`web/`。
- 不直接爬 Amazon 前台。
- 不暴露 Account-SK、API key、token、cookie。
- 不用新数据覆盖历史机会池；历史机会只更新状态和最近出现时间。
- 不把未通过类目/形态验证的产品放入最终机会池。

## 每周选品更新

入口命令：

```bash
python3 refresh_selection_workflow.py
```

执行链路：

```text
Sorftime MCP 类目扫描（类目树缓存、每类目一次 Top100、逐类目断点）
-> 候选产品导入
-> product_selection.py 初筛评分
-> auto_research_shortlist.py（MCP 迁移期间关闭自动 CLI 深挖）
-> category_shape_validation.py 类目/形态验证
-> build_dashboard.py 重建网页工作台
```

候选验证优先读取当前扫描目录 `archive/discovery_runs/<run_id>/raw_category_reports/` 中已保存的类目 Top100。同一最小类目的多个种子复用同一份报告，不产生额外 Sorftime 调用；结果按种子排名展示，并保留原始标题、ASIN、最小类目和提取后的产品形态。

候选池和利润口径：

- 全量合格候选按来源类目轮询抽样进入评分池：先让每个有候选的类目获得一个名额，再取各类目的第二、第三个产品。
- 运行归档同时保存全量去重候选 `candidates_eligible.csv` 和进入评分的 `candidates_selected.csv`。
- 默认成本估算只以较低权重参与排序；没有供应商真实报价时，不因估算毛利或估算单件利润直接淘汰。
- 供应商报价、头程和包装数据齐全后，恢复完整利润权重和毛利/单件利润门槛。

必须检查：

- `web/index.html` 是否重新生成。
- `reports/selection_ranked.csv` 是否有候选品。
- `data/category_shape_validation.csv` 是否有验证结果。
- `archive/selection_runs/YYYYMMDD_HHMMSS/` 是否新增快照。
- `archive/category_shape_runs/YYYYMMDD_HHMMSS/` 是否新增快照。
- `archive/shape_opportunity_library.csv` 是否只保留通过验证或值得观察的形态机会。

每周更新汇报必须包含：

- 本周扫描了多少类目。
- 新增了多少候选产品。
- 通过验证进入机会池的有几个。
- 被淘汰的主要原因。
- 是否有需要人工复核的产品。
- 网页是否已更新。

如果 Sorftime 或脚本失败，保留旧文件，说明失败命令、错误类型和未更新的输出；不要手工删除或覆盖旧结果。

MCP 运行口径：

- 密钥仅从 `SORFTIME_MCP_URL` 或用户级 `~/.codex/config.toml` 读取，不写入仓库、日志或归档。
- 每个类目调用一次 `category_report`，一次返回最多 100 个产品；100 类目的标准周扫约消耗 100 次调用。
- 原始结果写入 `archive/discovery_runs/RUN_ID/raw_category_reports/`，规则调整优先回放这些数据，避免重复调用。
- 回放命令：`python3 refresh_selection_workflow.py --replay-source-run archive/discovery_runs/RUN_ID --run-id NEW_RUN_ID`。
- 旧单品研究链路尚未迁移到 MCP；在迁移完成前，周扫不自动启动依赖 CLI 的深挖。

### 周扫可观测性和告警

`refresh_selection_workflow.py` 在成功和失败路径都会写入固定格式汇报：

```text
reports/weekly_scan_report.json
reports/weekly_scan_report.md
archive/weekly_scan_reports/YYYYMMDD_HHMMSS/
```

汇报字段用于对账：

- 扫描类目数：当前 Run 的 `archive/discovery_runs/RUN_ID/run_manifest.json` 成功类目数。
- 候选产品数：当前 Run 的 `archive/selection_runs/RUN_ID/selection_ranked.csv` 行数。
- 候选类目覆盖：当前 Run 的 discovery manifest 中 `represented_categories`，用于发现候选池被少数类目占满的问题。
- 新增候选入档数：`archive/opportunity_library.csv` 中 `archive_last_run_id` 等于最新初筛 run 且 `archive_seen_count=1` 的数量。
- 通过验证进入机会池：`archive/shape_opportunity_library.csv` 中 `archive_status=active_in_latest_run` 的数量。
- 本次形态机会：`data/category_shape_validation.csv` 中 `shape_recommendation=Shape opportunity` 的数量。
- 人工复核/补数据：`shape_recommendation` 为 `Watch shape` 或 `Needs category Top100` 的数量。
- 最新初筛快照、最新类目/形态快照、`web/index.html` mtime、当前日志路径。
- 初筛和形态淘汰主因 Top 5。

周报默认写入本地 `reports/` 和 `archive/weekly_scan_reports/`。

健康检查默认阈值：

- 连续 3 个最新扫描日 `Shape opportunity=0`：critical。
- `web/index.html` 超过 8 天未更新：critical。
- `logs/weekly_category_scan*.log` 中超过 8 天没有成功完成标记，或从未出现成功完成标记：critical。
- 单次最新扫描日入池为 0 但未达到连续阈值：warning。

阈值可用环境变量覆盖：

```text
AMZ_HEALTH_ZERO_POOL_WEEKS=3
AMZ_HEALTH_STALE_DAYS=8
```

失败路径会在同一份报告中写入 `failed_step` 和错误摘要。失败报告只读取当前 Run，不混入上次成功扫描的数据。

不调用 Sorftime 的本地验证命令：

```bash
python3 refresh_selection_workflow.py --report-only --no-issue-comment
python3 refresh_selection_workflow.py --report-only --mock-failure-step mock_failure --no-issue-comment
```

第一条验证成功汇报路径，第二条验证失败汇报路径；两者只读取本地现有输出，不消耗 Sorftime 额度。

固定 fixture 的入池/淘汰 smoke test：

```bash
python3 tests/smoke_selection_workflow.py
```

该命令只写入 `tmp/smoke_selection_workflow/<run_id>/`，不调用 Sorftime，不改 live `reports/`、`data/`、`web/` 或正式 `archive/`。验收口径：

- 初筛 fixture 共 3 个候选：1 个 `Watch or collect more data`，2 个 `Reject`。
- 初筛临时机会档案 `tmp/.../archive/opportunity_library.csv` 中 `active_in_latest_run=1`。
- 类目/形态验证 1 条记录，`Shape opportunity=1`。
- 临时形态机会池 `tmp/.../archive/shape_opportunity_library.csv` 中 `active_in_latest_run=1`。

迁移和重构验收基线见：

```text
docs/MIGRATION_BEHAVIOR_BASELINE.md
```

### 本机定时任务

每周类目扫描的本机 launchd 配置在：

```text
automation/com.amz-selection.weekly.plist
automation/run_weekly_category_scan.sh
```

默认计划是每周一 08:30 本地时间运行 `refresh_selection_workflow.py`。定时日志写入：

```text
logs/weekly_category_scan.latest.log
logs/weekly_category_scan_YYYYMMDD_HHMMSS.log
logs/launchd.weekly_category_scan.out.log
logs/launchd.weekly_category_scan.err.log
```

排查自动化时先看 latest 日志，再确认 launchd 是否加载：

```bash
launchctl print gui/501/com.amz-selection.weekly
```

定时脚本会把当前日志路径通过 `AMZ_WEEKLY_LOG_FILE` 传给 Python 入口，报告中应能看到同一个日志文件路径。

### 重复触发和冲突快照处理

`automation/run_weekly_category_scan.sh` 有两层重入保护：

- `logs/weekly_category_scan.lock` 防止同一时间并发运行。
- 当天已有完整 `archive/selection_runs/YYYYMMDD_*` 和 `archive/category_shape_runs/YYYYMMDD_*` 快照，且 `web/index.html` 存在时，脚本直接退出，不再调用 Sorftime，也不写入新快照。

如果确实需要同一天重跑，先在运行记录里写明原因，再用以下任一方式强制执行：

```bash
automation/run_weekly_category_scan.sh --force
AMZ_WEEKLY_FORCE=1 automation/run_weekly_category_scan.sh
```

同一天已经出现多份快照时，不删除历史目录。先对比以下信息：

- 两份 `run_meta.json` 的 `run_id`、`generated_at`、`candidate_count`、`archived_candidate_count`。
- `source_candidates.csv`、`selection_ranked.csv`、`category_shape_validation.csv` 的行数和 hash。
- `reports/selection_ranked.csv`、`data/category_shape_validation.csv`、`web/index.html` 的 mtime 是否和某个快照一致。

默认以最后一个完整快照且与当前 live 文件一致的快照作为临时权威口径；如果证据表明最后一次是误触发或异常输出，必须在 Issue 中明确声明改用哪个旧快照，并说明是否需要恢复 live 文件。无论选择哪份，都要在原 Issue 补一条结论评论，列出权威快照、权威指标、另一份快照的解释和后续处理。

## 单品深度研究

入口命令：

```bash
python3 refresh_product_research.py --asin ASIN
```

该命令会：

- 拉取 Sorftime 单品、关键词、竞品、评论和图片相关数据。
- 更新 `research/ASIN/` 最新研究数据。
- 归档到 `archive/product_research_runs/ASIN/YYYYMMDD_HHMMSS/`。
- 更新 `archive/product_research_index.csv`。
- 生成 `web/research/ASIN.html`。
- 重建 `web/index.html`。

单品研究结果必须覆盖：

- 产品形态、材质、颜色、套装数量、功能、尺寸、配件/元素。
- 图片特征和可视化标签。
- 评论痛点、用户最在意的点。
- 竞品结构和低评高销机会。
- 可切入机会。
- 是否值得继续打样或找供应商。

如果只是把已有本地研究补登记到档案，不重新调用 Sorftime：

```bash
python3 refresh_product_research.py --asin ASIN --register-existing
```

## 规则调整

规则调整先判断影响面，再改配置或脚本。

常见规则文件：

```text
config/autodiscovery_rules.json              类目发现和候选导入过滤
config/scoring_rules.json                    初筛评分权重、硬排除和推荐阈值
config/category_shape_validation_rules.json  类目/形态验证阈值
config/opportunity_research_rules.json       单品研究采集和形态判断
config/deep_dive_rules.json                  竞品深挖策略
config/import_defaults.json                  成本、物流、FBA 等估算默认值
```

当前固定阈值：

- 初筛总分权重：需求 0.15、竞争 0.30、利润 0.35、差异化 0.10、风险控制 0.10。
- Go：分数 `>=70`、毛利率 `>=35%`、单件毛利 `>=$8`、合规风险 `<=40`。
- Watch：分数 `>=52`、毛利率 `>=30%`、单件毛利 `>=$8`、平均评论数 `<=600`、大件风险 `<=60`。
- 硬停止：合规风险 `>=80`、大件风险 `>=65`、易碎风险 `>=80`、季节性风险 `>=80`。
- 食品接触：食品收纳、厨具/餐具、盐胡椒工具、食品加工工具、酒嘴/冰桶和可食用材料等合规风险至少 90，不进入个人卖家自动研究名单。
- 大件判定：硬大件词、容量 `>=50 quart`、`>=40 L`、“超大 + 箱/桶/篮/收纳容器”组合词、长柄拖把/扫把/清洁刷、拖把桶套装、4 件及以上大收纳套装或重量 `>=5000` 时大件风险至少 85；容量 `>=20 quart`、`>=25 L`、桶/盆 `>=10 quart`、最大边 `>=24 in`、软大件词或重量 `>=2000` 时大件风险至少 65；重量 `>=1000` 时至少 45，普通产品最大边 `>=45 in` 时至少 60。
- 类目证据：类目为空、`Unknown` 或类似 `['', '']` 时初筛硬停止，且不启动 Top100 自动研究。
- 类目/形态：类目评论中位数 `>=1000` 或 Top10 评论中位数 `>=1500` 视为评论墙；Top 品牌份额 `>=35%` 视为品牌集中。
- 形态入池：形态样本 `>=2`、形态月销均值 `>=150` 或形态总月销 `>=800`、形态评论中位数 `<=300` 或低评高销样本 `>=2`；同时检查 Top10 和形态 Top3 销量集中度。种子形态 `shape_score >=65` 才是 `Shape opportunity`。
- 只有 `Shape opportunity` 写入 `archive/shape_opportunity_library.csv`；`Watch shape` 和 `Needs category Top100` 只作为人工复核/补数据项。

调整时必须确认：

- 页面文案是否只是展示层，还是底层筛选逻辑也要同步改。
- 新规则会让哪些产品进入或退出机会池。
- 是否影响历史档案字段解释。
- 是否需要重跑 `refresh_selection_workflow.py` 或只重建 `web/index.html`。

规则改动流程：

- 必须新建或使用一个独立规则调整 Issue，说明当前规则、拟改阈值、影响面和预期进入/退出机会池的产品。
- 不要把规则改动混进 infra、页面可读性、日志或归档维护 Issue。
- 修改后至少运行固定 fixture smoke test；如果改动会影响真实候选口径，还要重建报告或重跑周扫，并在 Issue 中列出对比结果。
- 完成后把 Issue 状态设为 `in_review`，交给项目审核智能体复核；未审核前不要把新阈值视为最终口径。


### 改动边界

- Infra 改动：调度、锁、日志、Issue 汇报、健康检查、Sorftime helper、归档路径。必须做 mock/dry-run 成功和失败验证；不应改变筛选阈值或机会池口径。
- 规则改动：`config/*.json`、评分/验证阈值、硬排除词、形态判断逻辑。必须说明影响哪些产品进入或退出机会池，必要时重跑周扫或至少重建报告。
- 单次运行处置：同日强制重跑、失败补跑、指定旧快照为临时权威口径、补登记单品研究。必须在 Issue 中说明原因和采用的快照，不把临时处置写成长期规则。

### Sorftime helper 重试边界

`import_sorftime_candidates.py` 中的 `call_sorftime()` 最多尝试 3 次，只对以下瞬态错误重试：

- 网络连接和 DNS 类：`ECONNRESET`、`ETIMEDOUT`、`ESOCKETTIMEDOUT`、`EAI_AGAIN`、`ENOTFOUND`。
- 明确超时：TLS handshake timeout、context deadline exceeded、network timeout。
- 明确 5xx 服务端错误。

以下错误不重试，直接失败并进入周扫失败报告：

- 401 / 403 认证或权限错误。
- 429、rate limit、quota 配额错误。
- Account-SK、认证失败、账号无效等配置问题。
- Sorftime CLI 进程即使返回 0，只要响应中的业务 `Code` 非成功值（例如 `694 Insufficient request quota`），仍按失败处理，不能解释为空类目。

瞬态错误每次重试都会写 stderr 日志，等待时间按 2 秒、4 秒递增。不要把 401/403/429 包装成普通网络失败，也不要用无限重试掩盖额度或认证问题。

周扫只有在成功类目数和返回产品数都大于 0 时才允许发布候选数据并推进轮换状态。全零结果、配额失败或认证失败必须保留上一份有效候选和类目报告；真正成功但没有候选的运行可以发布类目报告，但不能伪装成“市场没有机会”。

## 数据归档原则

初筛档案：

```text
archive/selection_runs/YYYYMMDD_HHMMSS/
archive/opportunity_library.csv
```

类目/形态机会池：

```text
archive/category_shape_runs/YYYYMMDD_HHMMSS/
archive/shape_opportunity_library.csv
```

单品研究档案：

```text
archive/product_research_runs/ASIN/YYYYMMDD_HHMMSS/
archive/product_research_index.csv
```

长期档案字段中的 `archive_status` 用于区分 `active_in_latest_run` 和 `not_in_latest_run`。旧机会没有在新一轮出现时，只更新状态，不删除记录。

### 归档和日志保留策略

当前脚本不自动删除历史归档，也不批量删除目录。保留策略如下：

- 最近 12 个扫描周：完整保留 `archive/selection_runs/`、`archive/category_shape_runs/`、`archive/weekly_scan_reports/`、`archive/product_research_runs/` 下的原始目录和文件。
- 超过 12 个扫描周的归档：默认仍原样保留。需要降低目录数量时，只能在独立 Issue 中按月创建压缩副本，例如 `archive/monthly/YYYY-MM/selection_runs_<YYYY-MM>.tar.gz`；压缩后不得自动删除原目录。
- 如果确实需要删除、替换原始结果目录，必须先停止并请求用户确认；确认前只能压缩或移动，不能删除。
- `archive/opportunity_library.csv`、`archive/shape_opportunity_library.csv`、`archive/product_research_index.csv` 是长期索引，不按周清理；旧记录只更新状态和最近出现时间。
- `logs/weekly_category_scan_YYYYMMDD_HHMMSS.log` 保留最近 12 周在 `logs/` 根目录；更早日志可按月移动到 `logs/archive/YYYY-MM/`，但至少保留当前最新成功日志和 `logs/weekly_category_scan.latest.log` 便于健康检查。
- `logs/weekly_category_scan.latest.log` 是便利指针，会被下一次运行覆盖；历史依据以时间戳日志和归档报告为准。
- 任何 housekeeping 命令都不得使用 `rm -rf` 或批量删除目录；如需落地 housekeeping 脚本，默认模式必须是 dry-run，并打印将压缩/移动的路径清单。

## 网页报告

网页工作台入口：

```text
web/index.html
```

重建命令：

```bash
python3 build_dashboard.py
```

页面应展示：

- 最新初筛候选。
- 类目/形态验证结果。
- 通过验证的机会池。
- 单品研究档案和独立研究页入口。
- 需要人工复核或供应商验证的下一步动作。

## 任务结束汇报

任务完成时简洁记录：

- 做了什么。
- 改了哪些规则或页面。
- 生成了哪些结果。
- 有没有失败或未完成部分。
- 下一步建议。

重要修改在提交前完成测试、结果对比和人工复核。
