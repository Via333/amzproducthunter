# AMZ 选品工程待办

本文档记录 shape-first 主链路完成后的独立工程任务。这些任务不得在没有单独验收的情况下改变机会池口径。

## 已在本次完成

- 旧机会纠错：旧大件、食品接触、品牌兼容件和泛化形态保留原行，但标记 `invalidated_by_rule`。
- 产品排除规则单一入口：新规则以 `config/product_exclusions.json` 为主，手动评分时仍兼容旧 config 的自定义字段。
- 形态分类器统一：周扫和单品研究共用 `product_taxonomy.py`，剥离品牌词，套装修饰词不再分裂形态 key。

## 待独立实施

1. 拆分 `build_dashboard.py`：把数据装载、区块渲染、CSS 和 JavaScript 分开。这是纯重构，要求拆分前后生成页行为一致。
2. 仓库体积与回放取舍：先核对 Sorftime 数据条款和单次 raw report 体积，再决定是否提交、使用 Git LFS 或仅本地保留。未确认前不公开分发原始报告和评论全文。
3. launchd 配置模板化：用安装脚本将仓库路径写入 plist，避免仓库文件固定某个用户的绝对路径。
4. 文档阈值自动校验：新增测试，确保 README/WORKFLOW 的数字与 `config/*.json` 同步。
5. 软信号展示：在类目表、形态卡和周报中补充 CN/HK 卖家占比、FBA 占比和评论-销量相关性，仅供人工判断，不参与打分。
6. 健康度前列类目的第二页：先确认 Sorftime MCP `category_report` 是否支持分页；仅对前 N 个类目增量读取，不对所有类目翻倍调用。
