# AMZ 选品自动化系统

这个项目用于维护 Amazon US 选品自动化流程：从 Sorftime 类目/产品池获取候选品，按评分规则做初筛，再用类目/形态验证过滤机会，最终把通过验证的产品沉淀进机会池和网页工作台。

当前项目定位是执行系统，不是最终选品审核系统。它负责持续扫描、归档、生成报告和辅助研究；是否打样、询价或进入供应商验证，需要人工或项目审核环节确认。

详细执行清单见 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

## 核心边界

- 不直接爬 Amazon 前台；周扫描优先通过 Sorftime MCP，旧的单品研究暂时仍依赖 Sorftime CLI。
- 不把 Account-SK、API key、token、cookie 写入 README、CSV、JSON 或报告。
- 不删除历史数据；新扫描只能追加快照或更新长期档案状态。
- 机会池只收通过类目/形态验证的产品，避免把普通初筛候选误当机会。
- 同一类目或相似形态按形态归类展示，避免重复研究。

## 标准入口

每周更新使用统一入口：

```bash
python3 refresh_selection_workflow.py
```

它依次执行：

```text
1. discover_sorftime_opportunities.py --strategy category --score
2. auto_research_shortlist.py（MCP 迁移期间关闭自动调用，候选保留人工深挖入口）
3. category_shape_validation.py
4. build_dashboard.py
```

单品深度研究使用：

```bash
python3 refresh_product_research.py --asin B0FS1YH17C
```

指定关键词做一次独立选品搜索：

```bash
python3 keyword_opportunity_search.py --keyword "bread storage bag"
```

只刷新网页工作台：

```bash
python3 build_dashboard.py
```

## 文件结构

```text
config/                              筛选、评分、导入和深挖规则
data/discovered_candidates.csv       最新一轮 Sorftime 发现候选
data/category_shape_validation.csv   最新类目/形态验证结果
reports/selection_ranked.csv         最新初筛评分结果
reports/category_shape_validation.md 最新类目/形态验证报告
reports/product_opportunity_*.md     单品研究报告
archive/selection_runs/              每次初筛扫描快照
archive/category_shape_runs/         每次形态验证快照
archive/opportunity_library.csv      初筛候选长期档案
archive/shape_opportunity_library.csv 通过验证的形态机会池
archive/product_research_runs/       单品研究历史快照
archive/product_research_index.csv   单品研究索引
archive/category_scan_state.csv      类目轮换状态，记录上次扫描和累计次数
archive/keyword_search_runs/         每次关键词搜索完整快照
archive/keyword_search_index.csv     关键词搜索历史索引
research/ASIN/                       最新单品研究数据
web/index.html                       本地网页工作台
web/research/ASIN.html               单品研究页面
automation/                          每周类目扫描定时任务配置
logs/                                定时任务运行日志
```

## 每周自动化

本机定时任务配置在：

```text
automation/com.amz-selection.weekly.plist
automation/run_weekly_category_scan.sh
```

默认通过 macOS launchd 每周一 08:30 本地时间运行一次统一入口。运行日志写入 `logs/weekly_category_scan.latest.log` 和对应时间戳日志。脚本会检查 `web/index.html`、初筛结果、类目/形态验证结果和形态机会池是否生成。

安装、重载和人工检查命令见 [automation/README.md](automation/README.md)。

## 评分和验证

初筛评分由 `product_selection.py` 和 `config/scoring_rules.json` 控制：

```text
机会分 = 需求分 * 15%
      + 竞争分 * 30%
      + 利润分 * 35%
      + 差异化分 * 10%
      + 风险控制分 * 10%
```

初筛推荐值：

- `Go to supplier validation`: 分数、毛利和风险初步达标，进入供应商验证候选。
- `Watch or collect more data`: 有机会，但需要补竞品、成本、合规或形态证据。
- `Reject`: 利润、竞争、风险或硬排除规则不达标。

类目/形态验证由 `category_shape_validation.py` 和 `config/category_shape_validation_rules.json` 控制。只有验证结果为 `Shape opportunity` 或值得继续观察的形态，才会进入 `archive/shape_opportunity_library.csv`，并在网页的机会池中展示。

评分同时输出 `evidence_confidence` 和 `evidence_grade`。采购成本、头程或 FBA 费仍为默认估算时，页面必须显示 `estimated`；估算利润只用于排序，不能替代供应商报价。

## 从 Sorftime 自动导入

不想手填候选品时，优先用 Sorftime MCP，不建议直接抓 Amazon 页面。

当前已加导入器：

```text
discover_sorftime_opportunities.py
import_sorftime_candidates.py
config/autodiscovery_rules.json
config/import_defaults.json
config/sorftime_queries.example.csv
data/sorftime_response.example.json
```

### 推荐方式：不知道品类时自动扫市场

这才是真正的选品自动化入口：

```bash
python3 discover_sorftime_opportunities.py --score
```

它会做五步：

```text
1. 从本地缓存读取 US 站完整类目树
2. 按 config/autodiscovery_rules.json 和 config/category_exclusions.json 永久跳过高合规、高物流、强专利/品牌垄断类目
3. 优先扫描从未扫描的最小类目；全部覆盖后按最久未扫描轮换
4. 对每个类目调用一次 MCP `category_report`，每类目最多查看 100 个 Top 产品
5. 抓取候选并生成评分和网页
```

输出：

```text
reports/discovered_categories.csv
archive/category_scan_state.csv
archive/discovery_runs/RUN_ID/raw_category_reports/
data/discovered_candidates.csv
reports/selection_ranked.csv
reports/selection_report.md
archive/selection_runs/YYYYMMDD_HHMMSS/
archive/opportunity_library.csv
```

MCP 密钥只保存在用户级 `~/.codex/config.toml`，不能写入仓库。每次真实周扫的
`run_manifest.json` 会记录 `data_provider=mcp` 和 `provider_call_count`。如需调整规则后重算，
使用已归档的原始报告回放，不重复消耗额度：

```bash
python3 refresh_selection_workflow.py \
  --replay-source-run archive/discovery_runs/RUN_ID \
  --run-id NEW_RUN_ID
```

### 更新和归档机制

每次运行 `product_selection.py` 都会自动做两层保存：

```text
archive/selection_runs/YYYYMMDD_HHMMSS/selection_ranked.csv
archive/selection_runs/YYYYMMDD_HHMMSS/selection_report.md
archive/selection_runs/YYYYMMDD_HHMMSS/source_candidates.csv
archive/opportunity_library.csv
```

- `selection_runs` 是每次扫描的完整快照，后续更新不会覆盖旧快照。
- `opportunity_library.csv` 是长期机会档案，只收录 `Go to supplier validation` 和 `Watch or collect more data` 这类值得继续看的产品。
- 同一个 ASIN 会合并记录，保留首次发现、最近出现、出现次数、历史最高分和当前状态。
- 如果某个产品新一轮没出现，不会被删除，只会标成 `not_in_latest_run`。

如果只是临时测试、不想写入档案，可以加：

```bash
python3 product_selection.py --input data/discovered_candidates.csv --no-archive
```

日常更新可以直接跑统一入口：

```bash
python3 refresh_selection_workflow.py
```

它会依次完成 Sorftime 扫描、评分归档和网页重建。

## 单品研究入口和归档

当某个 ASIN 值得继续研究时，用这个入口指定：

```bash
python3 refresh_product_research.py --asin B0FS1YH17C
```

它会完成：

```text
1. 运行 product_opportunity_research.py 拉关键词、竞品、评论和图片
2. 写入 research/ASIN/ 的最新研究数据
3. 复制一份快照到 archive/product_research_runs/ASIN/YYYYMMDD_HHMMSS/
4. 更新 archive/product_research_index.csv
5. 生成 web/research/ASIN.html 独立研究页
6. 重建 web/index.html
```

已经研究过的单品不会因为选品初筛更新而丢失。网页里的「单品研究入口和档案」会列出所有研究过的 ASIN，并提供独立研究页入口。

单品研究同时输出四类证据：市场机会、产品切入口、经济可行性和证据置信度。市场结构包括销量集中度、价格带、新品渗透和评论/销量关系；商业可行性包括贡献利润、盈亏平衡 ACoS、MOQ、首批资金和现金周期。

已有研究不重新调用 Sorftime，只重建派生分析：

```bash
python3 refresh_research_analytics.py
```

如果只是把已有研究注册进档案，不重新调用 Sorftime：

```bash
python3 refresh_product_research.py --asin B0FS1YH17C --register-existing
```

默认规则偏向：

```text
小件
非食品
非药品/保健品
非电池/危险品
非强监管
非明显大品牌/官方品牌
售价 $12-$80
月销量 200-5000
review 不超过 2500
评分 3.6-4.6
```

这些规则都在这里改：

```text
config/autodiscovery_rules.json
config/scoring_rules.json
```

如果只想先看系统会扫哪些类目，不拉产品：

```bash
python3 discover_sorftime_opportunities.py --dry-run
```

如果 Sorftime 的类目产品接口字段和默认配置不同，优先改这里：

```json
{
  "category_products": {
    "method": "CategoryProducts",
    "payload_template": {
      "nodeId": "{category_id}",
      "page": 1
    }
  }
}
```

Sorftime endpoint 严格区分大小写，例如 `CategoryTree`、`CategoryProducts`、`KeywordSearchResults`、`ProductRequest`。

例如实际接口用 `nodeid`，就改成：

```json
{
  "nodeid": "{category_id}",
  "page": 1
}
```

### 方式一：用 Sorftime CLI 直接拉数据

Sorftime 官方 CLI 文档示例是：

```bash
npm install -g sorftime-cli
sorftime add myaccount your-account-sk
sorftime api ProductRequest '{"asin":"B0CVM8TXHP"}' --domain 1
```

本机已经安装过 `sorftime-cli` 时，只需要配置一次 Account-SK：

```bash
sorftime add myaccount your-account-sk
sorftime use myaccount
sorftime whoami
```

不要把 Account-SK 写进 CSV、README 或配置文件。

注意：Sorftime 的 `MCP Account-SK` 和 `APIs Account-SK` 是两套不同的 key。当前脚本走 `sorftime-cli`，必须使用 `APIs Account-SK`。如果把 MCP key 配进 CLI，API 调用会返回 `401`。

配置后先用一个低成本接口验证鉴权：

```bash
sorftime api ProductRequest '{"asin":"B0CVM8TXHP","trend":2}' --domain 1
```

如果这里返回 `401`，说明当前 Account-SK 没有通过 Sorftime API 鉴权。常见原因：

- 复制的不是 API/CLI 数据服务里的 Account-SK。
- Account-SK 少复制了一段或包含了多余空格。
- 账号未开通 Amazon US API/CLI 数据服务权限。
- 当前 profile 不是你想用的账号，可用 `sorftime list` 和 `sorftime use myaccount` 检查。

只有这个测试命令返回 JSON 且 `code` 为 `0` 后，自动发现脚本才能继续跑。

安装和配置好以后，使用独立关键词选品入口：

```bash
python3 keyword_opportunity_search.py --keyword "desk cable management tray"
```

它会拉取最多 100 个相关产品、应用当前个人卖家过滤和统一评分、保存每次搜索档案，并刷新网页。关键词初筛结果仍需做最小类目/形态验证，不能直接进入正式机会池。

底层也可以这样直接导入关键词搜索结果：

```bash
python3 import_sorftime_candidates.py \
  --method KeywordSearchResults \
  --payload '{"keyword":"desk cable management tray","pageIndex":1,"pageSize":20}' \
  --domain 1 \
  --limit 20 \
  --category Office \
  --score
```

生成：

```text
data/sorftime_candidates.csv
reports/selection_ranked.csv
reports/selection_report.md
```

### 方式二：批量跑多个关键词或类目

先编辑：

```text
config/sorftime_queries.example.csv
```

然后运行：

```bash
python3 import_sorftime_candidates.py \
  --queries config/sorftime_queries.example.csv \
  --output data/sorftime_candidates.csv \
  --score
```

### 方式三：先用导出的 JSON 测试

如果你已经能从 Sorftime 拿到 JSON，可以先保存成文件，再导入：

```bash
python3 import_sorftime_candidates.py \
  --from-json data/sorftime_response.example.json \
  --output data/sorftime_candidates.csv \
  --score
```

导入器会自动寻找包含 `asin`、`title`、`price`、`sales`、`review_count`、`rating` 这类字段的产品列表。

### 需要注意

Sorftime 能给我们价格、销量、review、评分、关键词等市场数据；但采购价、头程、FBA 费用通常还需要供应链数据。现在导入器会先用 `config/import_defaults.json` 里的比例估算成本，用于早期筛选。进入打样前必须换成真实报价。

### 为什么不是从关键词开始

关键词入口适合“验证一个方向”，比如你已经知道想看 `desk cable management tray`。

不知道做什么品类时，应该从类目/市场池开始：

```text
类目树 -> 过滤高风险市场 -> 类目产品池 -> 产品评分 -> 供应商验证
```

也就是说，系统保留两条入口：不知道做什么时用每周类目轮换；已有方向时用关键词入口验证。两条入口最后都必须经过类目/形态验证。

## 网页版工作台

当前项目已经增加本地网页入口：

```text
web/index.html
```

它把下面这些结果合并到一个页面里：

- 选品挖掘结果：机会分、利润、销量、评论、风险、listing 链接。
- 竞品深挖结果：direct / keyword / noise 竞品分类、评论门槛、品牌集中度、CN/HK 卖家占比。
- 单品机会研究：关键词、产品形态、图片识别、材质证据、套装数量、闭合方式、切入建议。
- 第二个项目下一步：痛点验证、供应商验证、差异化规格方向。

重新生成网页：

```bash
python3 build_dashboard.py
```

打开：

```text
web/index.html
```
