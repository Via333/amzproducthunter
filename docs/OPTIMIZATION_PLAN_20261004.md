# AMZ 选品系统优化改动说明（2026-10-04）

> 给执行者：这是一份基于代码审查的改动清单。开始前请先读 `PROJECT_CONTEXT.md`、`README.md`、`docs/WORKFLOW.md`。
> 文中行号对应 2026-10-04 本地工作区版本（分支 `codex/publish-github-pages` + 未提交改动），可能有偏移，以函数名为准。
> 请按阶段顺序做，每个阶段单独提交，每个阶段结束跑全部测试。

## 通用约束（必须遵守）

- 不删除任何历史数据、归档、研究目录；只追加或更新状态。
- 测试和规则验证一律不调用 Sorftime。规则改动用归档原始报告回放验证：
  `python3 refresh_selection_workflow.py --replay-source-run archive/discovery_runs/<RUN_ID> --run-id <NEW_RUN_ID>`
- 不把任何 key / token / webhook URL 写进仓库，统一从环境变量或用户级配置读取。
- 所有新增阈值写进 `config/*.json`，代码里只保留默认值。
- 每项改动配单元测试；修改评分逻辑时，在提交说明里附回放前后的漏斗对比（见文末"验收方式"）。

---

## 背景：两个已核实的事实

**事实 1：2026-09-28 周扫部分失败却报成功，并覆盖了线上数据。**

- 日志 `logs/weekly_category_scan_20260928_073040.log`：前 69 个类目连续报 `Sorftime MCP request failed after 3 attempts: SSL UNEXPECTED_EOF`（约 30 分钟网络中断），后 31 个成功，最后打印 "completed successfully"。健康检查只给了一个 warning。
- 成功的 31 个类目全部在 `Dinnerware & Serveware` 下。`product_risk.py` 的 `tableware` 正则（约第 32 行）会匹配 "dinnerware"，而 `combined_text()`（约第 68 行）把类目路径也拼进了匹配文本，所以每个产品都被判合规风险、被排除。330 个形态行 100% 被否，额度白花。
- 结果：
  - `data/discovered_candidates.csv` 现在是空的（0 行）；
  - `data/category_shape_validation.csv` 被覆盖；
  - `archive/shape_opportunity_library.csv` 中 33 条机会全部被降级为 `not_in_latest_run`；
  - 网页机会池默认筛选 `active_in_latest_run`（`build_dashboard.py` 约第 3403 行），所以现在显示为空。

**事实 2：9/21 正常周扫的漏斗（按现有代码逐行重算，与存档结论一致）。**

- 100 个类目 → 9,880 个产品 → 1,268 个形态行。
- 结果分布：Needs classification 807 / Reject 453 / Watch 7 / Opportunity 1。
- 1,032 行（81%）分类为 unresolved，覆盖全部形态 58% 的月销。
- 453 个拒绝的决定性原因：

| 决定性原因 | 行数 |
|---|---|
| 排除产品占比 ≥0.5 | 262 |
| 品牌集中 + 低评高销 <2 | 95（其中 85 行组内产品 ≤3） |
| 兼容/替换他牌配件 | 75 |
| 新品没卖起来 | 33 |
| 仅分数不够 | 7 |

- 只差一个门槛就能成为机会的有 25 行，其中 22 行卡在分类上。例如：
  - Manual Weeders：43 个产品，月销 42,092，$14.99；
  - Rain Gauges：月销 28,843；
  - Outdoor Statues 下的 sculpture：月销 79,857，评论中位 210。

---

## 阶段 0：先保护现有代码

**0.1 提交当前工作区**

- 现状：
  - 本地有 57 个非 web 文件和 git 版本不一致。
  - 新模块未跟踪，例如 `product_family.py`、`preview_service.py`、`new_entrant_research.py`、`research_*.py`、`review_insights.py`、`supplier_screening.py`、`title_product_names.py`、`dashboard_catalog.py`、`product_market_validation.py`。
  - 已修改的模块会 import 这些新模块，只提交一部分会让 HEAD 跑不起来。
- 要求：
  - 新建分支，把代码、配置、测试、文档整体提交。
  - 大体积图片和快照（`research/*/images`、`web/research/assets`、`archive/product_research_runs` 等）先不要提交，在 `.gitignore` 里明确处理。
  - 同一张图目前在 `research/`、`web/research/assets/`、`web/assets/research/` 存了三份，后续改成一份加引用。
- 确认 GitHub Pages 的可见范围：私有仓库的 Pages 站点默认对公网可见，`.github/workflows/pages.yml` 推送 main 时会发布整个 `web/`。在用户确认前，不要合并到 main。

**0.2 测试不依赖被忽略或生成的文件**

- 现状：
  - `tests/test_opportunity_evidence.py`、`tests/test_discovery_rotation.py`、`tests/smoke_selection_workflow.py` 写死了 `ROOT / "tmp"`，而 `tmp/` 被 gitignore 了。
  - 部分测试依赖生成出来的 `research/category_3397571_*`、`web/category/3397571.html`。
- 要求：
  - 改用 `tempfile.TemporaryDirectory()`。
  - 依赖的样例数据放进 `tests/fixtures/`。
- 验收：在一个全新 clone 里运行 `python3 -m unittest discover -s tests`，全部通过（当前共 283 个）。

---

## 阶段 1：周扫可靠性（P0，先保证数据可信）

### 1.1 失败率门槛：部分失败不得发布

- 现状：
  - `discover_sorftime_opportunities.py` 只在成功类目数为 0 时判失败（约第 924 行）。
  - `refresh_selection_workflow.py` 的 `validate_required_outputs()` 只检查 `products_examined > 0`（约第 112 行），随后直接把状态写成 success（约第 204 行）。
- 要求：
  - manifest 记录 `failed_categories`、`failed_category_share`。
  - 新增配置 `config/workflow_rules.json`：
    ```json
    "run_quality": {
      "max_failed_share_to_publish": 0.2,
      "max_failed_share_before_error": 0.5
    }
    ```
  - 失败率 > 0.2：状态记为 `partial`。结果只写入 `archive/discovery_runs/<RUN_ID>/` 和 `archive/category_shape_runs/<RUN_ID>/`，不更新 `data/`、`reports/` 下的"最新"文件，不更新机会池，不重建 dashboard 的当前视图；健康检查记 critical。
  - 失败率 > 0.5：进程以非 0 退出。
- 测试：用模拟 client 让 70% 类目抛错，断言 `data/category_shape_validation.csv` 和机会池文件没被修改，manifest 状态为 `partial`。

### 1.2 先写临时位置，校验通过再原子发布

- 现状：
  - `category_shape_validation.py` 在任何校验前就覆盖了 `output_csv`（约第 1503 行）。
  - `write_csv`、`build_dashboard.py` 写 `web/index.html`（约第 4390 行）、`category_scan_state.csv`（discover 约第 222 行，每完成一个类目就整文件重写一次）都不是原子写入。
- 要求：
  - 新增公共函数 `atomic_write_text(path, text)` / `atomic_write_csv(...)`：先写同目录临时文件，`os.replace()` 替换。
  - 以上几处全部改用它。
  - 发布"最新"文件的动作放在 1.1 的质量门槛之后。

### 1.3 机会池"有效"状态不能只看本周

- 现状：
  - `update_shape_archive()`（`category_shape_validation.py` 约第 1378–1388 行）把本轮没出现的形态一律标为 `not_in_latest_run`。
  - 轮换机制下每周扫的是不同类目，所以即使每周都正常，池子也只剩本周的机会。
- 要求：
  - 只有"本轮重扫了该类目、但该形态没有再通过"时才降级（状态名如 `failed_on_rescan`）。
  - 类目本轮没被扫到的，保持原状态，并新增字段 `last_validated_run_id`、`last_validated_at`。
  - 新增状态 `stale_needs_rescan`：超过配置的天数（如 `opportunity_stale_days: 90`）未重扫时使用。
  - dashboard 默认显示：有效 + 待重扫，不再只显示 `active_in_latest_run`。
- 一次性修复：用新逻辑重算现有 `archive/shape_opportunity_library.csv`，恢复被 9/28 降级的条目（不删行，只改状态，并在 `archive_notes` 记录原因）。

### 1.4 MCP 客户端重试与熔断

- 现状（`sorftime_mcp_client.py` 约第 122–142 行）：
  - 3 次尝试，间隔 2s / 4s。
  - 只捕获 `URLError`、`TimeoutError`、5xx。
  - `http.client.RemoteDisconnected`、`ConnectionResetError`、`IncompleteRead`、JSON 解析错误不会被捕获。`urlopen` 的 `getresponse()` 和 `read()` 阶段抛出的异常不会被包装成 URLError，而主循环（discover 约第 838 行）只捕获 `RuntimeError`，所以一次断连可能让整轮中止。
  - 会话过期（404）时不会重新 `initialize()`。
  - 429 不读 `Retry-After`。
  - 网络故障没有熔断。
- 要求：
  - 把 `OSError`、`http.client.HTTPException`、`ValueError`（解析失败）统一包装成可重试错误；最终抛出 `RuntimeError`。
  - 指数退避加抖动（如 2s、6s、18s，±30%）。
  - 404 或会话失效时清空 `_session_id`，重新 initialize 后重试一次。
  - 429 读 `Retry-After`，超过上限就熔断。
  - 在 discover 主循环加熔断：连续 N 个类目失败（配置 `circuit_breaker_consecutive_failures: 5`）时，暂停 `circuit_breaker_pause_seconds: 300` 后做一次连通性探测；仍失败则停止本轮，状态 `partial`，剩余类目不消耗额度。
  - 开跑前做连通性预检：调用一次轻量接口，失败则每 60 秒重试，最多等 15 分钟。
- 测试：用 mock 覆盖上述每种异常；连续失败触发熔断；会话重建。

### 1.5 告警推送

- 现状：
  - 健康检查只写日志，critical 不影响退出码。
  - `automation/run_weekly_category_scan.sh` 第 143 行传了 `--no-issue-comment`，唯一的通知渠道（Multica 评论）因此被关掉；通知失败也被吞掉。
- 要求：
  - 新增飞书机器人推送，webhook URL 从环境变量 `AMZ_ALERT_FEISHU_WEBHOOK` 读取；未配置时只写日志，不报错。
  - 每次周扫结束都推送一条摘要：状态、扫描/成功/失败类目数、失败率、形态行数、Opportunity / Watch 数、决定性淘汰原因 Top5（见 3.1）、dashboard 是否已更新。
  - critical 时进程 exit 2，推送标题标红。
  - "死人开关"：每天检查一次，若最近一次成功周扫超过 8 天，推送提醒。可以放在 1.6 的每日触发里一起做。

### 1.6 运行环境（launchd / 睡眠 / 锁）

- 现状：
  - `StartCalendarInterval` 在机器睡眠时会醒来补跑一次，关机时直接跳过。刚唤醒时网络往往还没就绪。
  - `automation/` 下有两个调度相同的 plist（`com.amz-selection.weekly`、`com.multica.amz-selection.weekly`），需要确认只加载了一个。
  - `shlock` 锁在 `cleanup()`（约第 60–66 行）里从不删除；PID 被别的进程复用时会误判"已在运行"，然后 exit 0，这一周被静默跳过。
- 要求：
  - 整个运行用 `caffeinate -i` 包住。
  - 改为每天触发一次，脚本内部判断"最近 7 天内已有成功（非 partial）周扫就退出"，以此实现关机或错过后的补跑。
  - `trap` 里删除本进程持有的锁。
  - 判断锁时校验 `ps -p <PID> -o command=` 是否为本脚本，或锁文件超过 6 小时视为过期。
  - 在 `automation/README.md` 写清楚只保留哪一个 plist。

---

## 阶段 2：选品规则（P1，提升真实机会产出）

每一项都需要用 9/21 的归档回放（`archive/discovery_runs/20260921_083005`）给出改动前后的漏斗对比。

### 2.1 品牌集中度加最小样本（改动小、收益大）

- 现状：
  - 形态层用 `family_top_brand_share`，来自 `category_metrics()`（约第 377–395 行），计算方式是 Top10 里头部品牌的"个数占比"，没有最小样本。
  - 组里 1 个产品就是 1.0，2 个产品就 ≥0.5，阈值 `category_top_brand_share: 0.35` 必然触发。
  - 9/21 组内产品 ≤2 的 821 行 100% 被打上 "brand concentration"，占全部该旗标的 89%。
- 要求：
  - 新增 `brand_share_min_count: 6`。family 样本不足时：
    - 不打 "brand concentration" 旗标；
    - 评分里的 `concentration_score` 改用类目级品牌占比（如有），否则给中性 50。
  - 品牌占比改为按月销占比计算（保留个数占比作为参考字段）。

### 2.2 分类兜底：不能让 81% 的行不评分

- 现状：
  - 标题分类识别失败时为 `Needs classification`，不评分、不入池（`evaluate_shape_row` 约第 1035 行）。
  - 泛标签列表有两份且不一致：`category_shape_validation.py` 的 `GENERIC_FORM_NAMES`（约第 146 行）和 `product_family.py` 的 `GENERIC`（约第 38 行）。
- 要求：
  - 标题规则识别失败时，按顺序兜底：
    1. 组内重复出现 ≥3 次的具体 product_type；
    2. 最小叶类目名本身（如 "Manual Weeders"、"Rain Gauges"）。
    得到的形态标 `classification_status = "category_proxy"`。
  - `category_proxy` 正常评分，可以进 Watch。进 Opportunity 需额外满足 `form_count >= 5`，并在网页标"待人工确认分类"。
  - 两份泛标签列表合并成一个来源（建议放进 `config/product_taxonomy.json`），两个模块都从这里读。
- 验收：回放 9/21 后，unresolved 行占比显著下降；上面列出的 Manual Weeders、Rain Gauges 等在结果里有分数和结论。

### 2.3 "排除产品占比 ≥50%"不再一票否决整个形态

- 现状：`excluded share >= form_excluded_share (0.5)` 属于 `always_reject_flags`（约第 977 行、第 1016 行）。有 20 行在剔除后仍剩 ≥3 个产品、月销 ≥800，照样被否。
- 要求：
  - 新增 `form_excluded_min_remaining: 5`。
  - 剔除后剩余合格产品 ≥5 时，只用剩余子集评估，旗标改为信息性的 "mixed with excluded products"，不进入 `always_reject_flags`。
  - 剩余 <5 时维持否决。
  - 当剩余合格产品为 0 时，只输出一个旗标 `all products excluded: <主要排除原因>`，不要再同时附带 "weak form demand"、"no listing-age data" 这类空样本推出来的旗标（第 939、963、977 行附近会机械地同时触发）。

### 2.4 类目预筛与产品级规则对齐，避免白花额度

- 现状：
  - `category_preference_score()`（discover 约第 195–211 行）会给路径里含 `prefer_name_contains` 词（home、kitchen 等）的类目每词加 10 分。9/28 因此选进了 55 个餐具和咖啡茶类目，产品层又被全部排除。
  - `product_risk.py` 的匹配文本包含类目路径，类目名一命中，整类产品都会被排除。
- 要求：
  - 产品级风险正则只匹配标题和品牌；类目路径单独用于类目预筛。
  - 在类目选择阶段（`category_exclusion_reason`）复用同一套高风险词，提前跳过整棵子树，例如 Dinnerware、Serveware、Coffee/Tea、Drinkware 等食品接触子树。具体清单请列出来，交给用户确认后写进 `config/category_exclusions.json`。
  - 自动学习排除：某类目扫描后产品排除率 ≥90%，自动写入一个"待确认排除"列表（新文件，不直接改正式配置），由 dashboard 展示给用户确认。
  - 从 `prefer_name_contains` 去掉 "kitchen"，或者降低加分，避免偏向食品接触类目。

### 2.5 形态层加入"到仓成本上限"门槛

- 现状：
  - 周扫形态层完全不算利润，只有 `$12–80` 价格带软旗标。
  - 候选导入阶段已有 `fba_fee`（`import_sorftime_candidates.py` 第 315–317 行：有实测用实测，没有就估算），但形态层的 `normalized_category_product()`（约第 402 行）没读这个字段。
- 要求：
  - `normalized_category_product()` 读取 category_report 原始数据里的 FBA 费字段，先在 `archive/discovery_runs/*/raw_category_reports/` 里确认字段名，并标注是实测还是估算。
  - 对每个产品算：
    ```
    到仓成本上限 = 售价 × (1 − 佣金率 − 广告率 − 退货损耗率 − 目标利润率) − FBA
    ```
    默认值：佣金 15%、广告 15%、退货 5%、目标利润 20%，全部放进配置。
  - 形态取中位数，写入新列 `form_landed_cost_ceiling_median`。
  - 新增门槛：
    - `landed_cost_ceiling_watch_below: 3.0`：低于 $3 时最多给 Watch，旗标 "thin unit economics"；
    - `landed_cost_ceiling_reject_below: 1.5`：低于 $1.5 直接拒。
  - 网页上标明这是"倒推预算，不是供应商报价"。和单品研究的利润口径区分开，不能互相覆盖。

### 2.6 新进入者成功判定加评论上限

- 现状：`new_entrant_metrics()`（约第 525–550 行）里的 `successes` 只看月销 ≥200。`new_entrant_max_reviews: 300` 只用于挑参考 ASIN，没用在成功判定上。老父体新加的子体（上架日期新、销量和评论是继承的）因此会被算成"新品成功"。
- 要求：
  - 成功判定改为月销 ≥ `new_entrant_min_sales` **且** 评论 ≤ `new_entrant_max_reviews`。
  - 有 `parent_asin` 或变体数字段时，父体比子体早上架超过 6 个月的，不计入新进入者。
  - 保留旧口径作为参考列 `form_new_entrant_success_count_raw`，便于对比。

### 2.7 人工结论回流到机会池

- 现状：
  - 工作台的"舍弃"记录只存在本机 `.local/research-workbench.sqlite3`，不影响评分和机会池。
  - 单品研究 `archive_status=rejected`（如调酒器套装 B0FFHG9N88）不会回写到对应的形态机会。
  - 已确认强季节性的渔网形态，在池里仍是有效状态。
- 要求：
  - 构建机会池或 dashboard 时读取：
    1. 工作台舍弃记录；
    2. `archive/product_research_index.csv` 里的研究结论；
    3. `research/*/required_checks.json` 里的季节性和合规结论。
  - 对应形态标 `suppressed_by_user`、`rejected_by_research`、`seasonal_confirmed` 等状态，并写明来源。只改状态，不删行。
  - 同一产品身份的形态在后续周扫里再次出现时，继承上述状态，不自动恢复为有效。

### 2.8 轮换策略：新类目加定向重扫

- 现状：`category_scan_state.csv` 共 865 行，812 个已扫，全部只扫过 1 次，从未重扫。排序只看"从未扫过优先"加偏好词得分。
- 要求：
  - 先跑 `discover_sorftime_opportunities.py --dry-run`，在输出里打印排除后的可扫描类目总数和预计全覆盖周数。
  - 每周名额按配置拆分：`new_category_quota: 70`、`rescan_quota: 30`。
  - 重扫优先级：
    1. 出过 Opportunity 且超过 60 天未重扫；
    2. 出过 Watch 或单门槛近似漏网；
    3. 上一轮扫描失败的类目。
  - 新增 `consecutive_failures` 字段：连续失败 3 次的类目降级排后，避免永远占着队首。

---

## 阶段 3：小项

- **3.1 周报淘汰原因改为"决定性原因"。**
  - 现状：`weekly_scan_observability.py` 的 `top_reject_reasons()`（约第 121–130 行）和 `run_weekly_category_scan.sh` 的 `top_shape_reject_reasons()`，把被拒行身上的所有旗标都计了一遍。
  - 要求：在 `evaluate_shape_row()` 里写入 `decisive_reject_reason` 列，记录真正导致拒绝的那一条分支；周报、日志、飞书推送都用这一列。
- **3.2 同日补跑。** `run_weekly_category_scan.sh` 的 `discovery_run_succeeded()` 和约第 122 行的同日判断，会把部分成功也当成"今天已完成"。改为只有 status=success（非 partial）才算完成。
- **3.3 日志时区。** 日志时间统一带 `+0800` 或用 UTC。机器在外地时（9/28 是 +0700），"同一天"判断不能因此出错。

---

## 验收方式

1. 全新 clone 中 `python3 -m unittest discover -s tests` 全部通过，并新增以下测试：失败率门槛、原子发布、机会池状态、MCP 异常与熔断、品牌最小样本、分类兜底、排除子集评估、到仓成本门槛、新进入者口径、人工结论回流。
2. 零 Sorftime 调用回放 9/21：
   ```bash
   python3 refresh_selection_workflow.py \
     --replay-source-run archive/discovery_runs/20260921_083005 \
     --run-id 20260921_083005_optimization_replay
   ```
   在提交说明里附对比表：形态行数、unresolved 占比、Needs classification / Reject / Watch / Opportunity 数量、决定性淘汰原因 Top5，以及新增或消失的 Opportunity / Watch 清单和原因。
3. 回放 9/28 的 31 个成功类目，确认：
   - 不会出现 330 行都带同样三个旗标的情况；
   - 若用模拟方式还原 69 个失败，线上 `data/` 和机会池不被覆盖。
4. 机会池一次性修复后，dashboard 默认视图能看到 9/28 之前的有效机会。

## 不要做的事

- 不要为了"多出机会"直接调低 `shape_opportunity_score`、`watch_shape_score` 等总分门槛。先修 2.1–2.3 的机制问题，再看分布。
- 不要放宽食品接触、易碎、大件、强合规、兼容他牌配件这些经营限制。
- 不要把倒推的到仓成本上限写成利润或供应商报价。
- 不要在网页重建、测试或回放中调用 Sorftime。
