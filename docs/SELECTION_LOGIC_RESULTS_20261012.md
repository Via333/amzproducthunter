# 选品逻辑调整验收记录

日期：2026-10-12。分支：`claude/selection-logic-20261012`，基于 `codex/reliable-selection-20261004`。不合并 main，不触发 Pages 发布；本地原始数据和回放生成的页面不随本次提交上传。

## 改了什么

1. **节日季节性**（`seasonality.py`、`config/seasonality_rules.json`）
   - 只读标题，不调用 Sorftime。标题主体出现节日词，或节日词紧跟装饰名词（decor、ornament、lights、inflatable、wreath 等），才算节日主题商品。
   - 不算的情况：礼品定位（"Christmas gifts for women"）、顺带用途（"for Halloween" 后面没有装饰名词）、横跨 3 个及以上季节的通用款（情人节到圣诞都写上的门廊鹅衣服）。万圣节、感恩节、圣诞同时出现的仍算第四季度节日款。
   - 形态内节日主题销量占比 `>=60%`：`seasonal holiday products`，直接淘汰；未分类组和类目代理组也一样淘汰。
   - 占比 `>=30%`：`partly seasonal holiday products`，最高 Watch。
   - 类目健康度乘以 `(1 - 节日主题销量占比)`，并新增 `category_seasonal_sales_share` 列；占比 `>=30%` 打旗标。
   - 页面结论注明“万圣节主题商品占销量 X%，当前月销含节日旺季”。
2. **待复扫机会优先复扫**（`discover_sorftime_opportunities.rescan_priorities`）
   - 机会池里标为 `stale_needs_rescan` 的类目直接排在复扫名额最前面，不再等 60 天年龄门槛。
   - 有效机会仍按 60 天没有新证据排在最前，上一轮 Watch 类目其次。
3. **商品类型确认类目代理**（`category_shape_validation.form_summary_rows`）
   - 叶类目代理组满足以下三条时，标为 `product_type_confirmed`：
     - 合格样本 `>=5` 个；
     - 主导类型是一个具体的亚马逊商品类型（原始报告 `product_category`）；
     - 该类型同时占销量和数量 `>=80%`。
   - 确认后按正常形态规则评分，可能成为 Opportunity，也可能被淘汰。其余代理组仍最多 Watch。
   - `OUTDOOR_LIVING`、`HOME`、`ABIS_*` 这类部门级或占位类型不算确认；新增配置项为 `config/product_taxonomy.json` 的 `broad_product_types`。
   - 商品类型只能说明同用途，不能说明同结构、同规格。页面标签写“亚马逊商品类型一致，规格待核”；Opportunity 的下一步先抽查参考 ASIN 的主图和规格。
   - 这一项有意改变了 2026-10-04 记录的边界（“不把供应商类型标签当成已确认产品身份”）：放宽的只是同用途这一层，结构和规格仍要人工核验。

没有调低总分门槛，也没有放宽食品接触、易碎、大件、合规、品牌配件限制。确认后的代理组遇到排除产品占比高时，仍按严格的 `excluded products` 淘汰，不走已识别形态的“评估剩余子集”路径。

## 历史回放

9/21、10/5 两批原始 Top100 各回放两遍：`54e45dd` 原逻辑一遍，本分支新逻辑一遍。共四次运行，零 Sorftime 调用；原始归档未改，两次回放都没有发布。

10/5 原逻辑回放与已发布的 10/5 结果逐行一致：336 行推荐结论全部相同。所以下表的差异只来自本次改动。

| 指标 | 9/21 原逻辑 | 9/21 新逻辑 | 10/5 原逻辑 | 10/5 新逻辑 |
|---|---:|---:|---:|---:|
| 形态行 | 336 | 336 | 336 | 336 |
| unresolved | 0 | 0 | 0 | 0 |
| 类目代理（未确认） | 100（29.8%） | 75（22.3%） | 100（29.8%） | 64（19.0%） |
| 商品类型确认 | — | 25 | — | 36 |
| Needs classification | 34 | 32 | 10 | 10 |
| Watch | 100 | 83 | 111 | 91 |
| Opportunity | 0 | 1 | 0 | 0 |
| Reject | 202 | 220 | 215 | 235 |
| 节日主题淘汰（决定性原因） | — | 13 | — | 11 |

决定性拒绝原因 Top 5（新逻辑）：

- 9/21：分数不足 61、排除产品占比 25、新品未卖起 20、类目评论墙 17、全样本超大件 16。原逻辑依次为 61、25、19、17、16。
- 10/5：分数不足 65、排除产品占比 26、新品未卖起 22、类目评论墙 17、形态评论墙 15。原逻辑依次为 64、26、18、17、15。

节日主题淘汰有 13 行和 11 行，不在 Top 5 内。其中一部分原来已经是 Reject，只是决定性原因变了。

### 结论变化清单

9/21（19 行变化）：

| 类目 / 形态 | 分数 | 原结论 → 新结论 | 原因 |
|---|---:|---|---|
| Post Lights /（整类目代理） | 83.3 | Watch → Opportunity | 商品类型确认（light fixture 85%）后按正常规则通过 |
| Lighted Holiday Displays /（整类目代理） | 91.4 | Watch → Reject | 节日主题销量 99% |
| Inflatable Yard Decorations /（整类目代理） | 90.1 | Watch → Reject | 节日主题销量 94% |
| Lighted Holiday Displays / garland | 82.6 | Watch → Reject | 节日主题销量 100% |
| Outdoor Nativity Scenes / table runner | 71.1 | Watch → Reject | 节日主题销量 100% |
| Decorative Lighting Projectors /（整类目代理） | 52.4 | Watch → Reject | 节日主题销量 60% |
| Outdoor Nativity Scenes /（整类目代理） | 46.3 | Watch → Reject | 节日主题销量 98% |
| Outdoor Nativity Scenes / hanging kit | 42.7 | Watch → Reject | 节日主题销量 100% |
| Lighted Holiday Displays / floor broom | — | Needs classification → Reject | 节日主题销量 100%（万圣节巫婆扫帚） |
| Inflatable Yard Decorations / floor broom | — | Needs classification → Reject | 节日主题销量 100% |
| Cultivators & Tillers /（整类目代理） | 50.6 | Watch → Reject | 商品类型确认后：品牌集中 |
| Combination Grill-Smokers /（整类目代理） | 48.9 | Watch → Reject | 商品类型确认后：分数不足 |
| Grilling Charcoal /（整类目代理） | 48.7 | Watch → Reject | 商品类型确认后：品牌集中 |
| Hygrometers /（整类目代理） | 48.6 | Watch → Reject | 商品类型确认后：Top10 评论墙 |
| Trowels /（整类目代理） | 48.3 | Watch → Reject | 商品类型确认后：品牌集中 |
| Loppers /（整类目代理） | 48.1 | Watch → Reject | 商品类型确认后：品牌集中 |
| Indoor Thermometers /（整类目代理） | 46.7 | Watch → Reject | 商品类型确认后：类目评论墙 |
| Spades /（整类目代理） | 44.5 | Watch → Reject | 商品类型确认后：新品未卖起 |
| String Lights /（整类目代理） | 41.9 | Watch → Reject | 商品类型确认后：分数不足（另有 39% 节日主题） |

10/5（20 行变化，全部 Watch → Reject）：

| 类目 / 形态 | 分数 | 原因 |
|---|---:|---|
| Decorative Garden Stakes /（整类目代理） | 91.4 | 节日主题销量 80% |
| Garage Door Decorations /（整类目代理） | 86.9 | 节日主题销量 97% |
| Garage Door Decorations / banner | 78.8 | 节日主题销量 100% |
| Mailbox Covers / mailbox | 71.2 | 节日主题销量 74% |
| Plaques & Wall Art /（整类目代理） | 70.3 | 节日主题销量 62% |
| Mailbox Covers /（整类目代理） | 67.1 | 节日主题销量 70% |
| Garage Door Decorations / backdrop | 61.7 | 节日主题销量 100% |
| Card Albums /（整类目代理） | 54.7 | 商品类型确认后：分数不足 |
| Outdoor Clocks /（整类目代理） | 51.3 | 商品类型确认后：销量集中 |
| Address Signs /（整类目代理） | 50.4 | 商品类型确认后：销量集中 |
| Pasta Containers /（整类目代理） | 49.7 | 商品类型确认后：销量集中 |
| Wall-Mounted Wine Racks /（整类目代理） | 48.9 | 商品类型确认后：分数不足 |
| Thermoses /（整类目代理） | 46.3 | 商品类型确认后：分数不足 |
| Training Mats /（整类目代理） | 45.1 | 商品类型确认后：品牌集中 |
| Stabilizer Jacks /（整类目代理） | 44.3 | 商品类型确认后：新品未卖起 |
| Weathervanes /（整类目代理） | 44.1 | 商品类型确认后：新品未卖起 |
| Gel Ink Rollerball Pens /（整类目代理） | 40.9 | 商品类型确认后：分数不足 |
| Transmission Jacks /（整类目代理） | 40.4 | 商品类型确认后：分数不足 |
| Tumbling Mats /（整类目代理） | 34.4 | 商品类型确认后：新品未卖起 |
| Landing Mats /（整类目代理） | 30.7 | 商品类型确认后：新品未卖起 |

两批回放都没有 Opportunity 被移出。10/5 的 Opportunity 仍为 0。

节日占比在 30% 至 60% 的形态打 `partly seasonal holiday products`，最高 Watch。下面这些形态原来就是 Watch，这次结论没有变化：

- 9/21：Outdoor Statues 57%、Torches 45%、Outdoor Lighting Hooks 41%。
- 10/5：Yard Signs 43%、Flags / banner 48%、Garage Door Decorations / door 47%、Flags 31%。

### 两项必须一起上

在新逻辑里只关掉节日规则，9/21 的 Lighted Holiday Displays（91.4，万圣节灯饰 99%）和 Inflatable Yard Decorations（90.1，万圣节充气装饰 94%）会因为商品类型确认直接变成 Opportunity。10/5 会有 7 行节日形态回到 Watch。这个反事实检查重新评估了回放输出：不改任何数据重评，336/336 与原结论一致；只关掉节日阈值，才出现上述变化。

### 类目健康度

节日类目从健康度前列掉到末尾：

| 类目 | 节日主题销量 | 排名（原 → 新） | 健康分（原 → 新） |
|---|---:|---|---|
| 9/21 Outdoor Statues | 60% | 2 → 97 | 94.7 → 37.7 |
| 9/21 Lighted Holiday Displays | 99% | 3 → 99 | 94.3 → 0.8 |
| 9/21 Memorial Garden Stones | 26% | 8 → 60 | 88.8 → 65.4 |
| 9/21 Inflatable Yard Decorations | 91% | 24 → 98 | 78.3 → 7.4 |
| 10/5 Garage Door Decorations | 96% | 1 → 100 | 97.3 → 4.3 |
| 10/5 Decorative Garden Stakes | 82% | 2 → 99 | 94.2 → 17.0 |
| 10/5 Mailbox Covers | 75% | 3 → 98 | 93.3 → 23.4 |
| 10/5 Yard Signs | 38% | 7 → 81 | 92.3 → 57.3 |
| 10/5 Plaques & Wall Art | 44% | 14 → 91 | 86.6 → 48.3 |

10/5 新的健康度前三是 Outdoor Trash Cans 93.2、Pot Lid Holders 92.5、Cabinet Door Organizers 92.4。Memorial Garden Stones 被扣分，来自其中的万圣节墓碑道具。

### 节日识别抽查

- 9/21 有 18 个形态带节日旗标，10/5 有 15 个。只用标题主体里的节日词重算、不计用途短语，这 33 个形态的推荐结论全部不变。重算结果先与回放输出逐个核对过，节日占比一致。
- 用途短语主要影响 30% 至 60% 的封顶。例如 “for Hanging Christmas Lights”“for Halloween Decorations” 不计入后：
  - Torches 从 45% 降到 17%；
  - Outdoor Lighting Hooks 从 41% 降到 25%；
  - Flags 从 31% 降到 24%。
  这些形态本来就是 Watch。
- 已知误判：顺带列出节日的通用款会被计入，例如 “Solar Lanterns … for Christmas Decoration”、“Inspirational Rocks … Gifts & Easter Decorations”。这类商品在已检查的类目里占比都很小，没有改变任何结论。

### 轮换模拟

用 10/5 的扫描状态和机会池，模拟 2026-10-13 08:30 的下一轮选择：

- 原逻辑：16 个待复扫机会类目一个都没选中，30 个复扫名额全部给了上一轮 Watch 类目（共 83 个）。
- 新逻辑：16 个全部选中，剩余 14 个名额给上一轮 Watch 类目。新类目仍为 70 个。

## 验证

- 全套单元测试 325 项通过。新增内容：
  - `tests/test_seasonality.py`：标题识别、淘汰、封顶、代理淘汰、健康度扣减。
  - `tests/test_product_type_confirmation.py`：确认、部门级类型、混合或样本少、标题身份不被覆盖。
  - `tests/test_discovery_rotation.py`：待复扫优先。
- 固定离线烟测通过：2 类目、3 形态，Opportunity / Watch / Reject 各 1。
- 回放生成的 9/21、10/5 新页面在无头 Chromium 中加载，没有 JavaScript 错误；页面里能看到节日占比和“规格待核”说明。

## 已知限制

- 天气季节性（取暖器、喷雾风扇、泳池用品）没有覆盖。要可靠判断，需要每个类目一次 Sorftime `category_trend`，本次没有增加调用。10/5 的 Misting Fans 仍在健康度第 6，Patio Heaters 仍是 Watch。
- 节日占比按当次快照计算。2 月、3 月扫描时，万圣节商品可能已经不在 Top100，所以这个判断主要防的是秋冬季节的高估。
- 商品类型确认只说明同用途。light fixture 这类较宽的类型，在 Post Lights 叶类目里能排除灯泡和配件，但太阳能柱头灯和有线柱灯仍可能混在一起，所以必须先抽查参考 ASIN。
- 页面上的旗标仍是英文原文（如 `seasonal holiday products`），中文说明写在结论文字里。
