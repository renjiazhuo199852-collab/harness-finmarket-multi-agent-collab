# FX Debate 前三 Agent 生产化设计调研

> 范围：`pair_bull`、`pair_bear`、`macro_technical`。本文是研究与方案建议，不涉及下单系统，也不把任何历史关系解释为确定收益。

## 1. 结论先行

推荐采用 **“冻结的 Evidence Factory + 镜像假设 Agent + 中立状态 Agent”**：运行前由确定性代码构造同一份 point-in-time（PIT）证据包；Bull/Bear 在完全对称的证据、预算和输出契约下分别寻找“上涨/下跌假设何时成立”；Macro & Technical 不投第三张方向票，而是输出相对宏观状态、技术状态、冲突与事件风险。LLM 只负责有边界的解释、反证和情景组合，指标、版本、可得时点、分数和校验均由代码负责。

上线优先级应是：**时点/版本语义 > 数据覆盖 > 输出契约 > Prompt > 模型升级**。在 PIT 数据未验收前，只能做实时 shadow test，不能用当前最新值回填历史并声称回测有效。

## 2. 对现有 MVP 的代码审计

已有基础值得保留：不可变 `EvidenceContext`、稳定 evidence ID、三 Agent 并行隔离、Tool 白名单、Pydantic 契约和最终确定性校验。现状见 [`fx_debate_team.yaml`](../../agent/src/swarm/presets/fx_debate_team.yaml)、[`models.py`](../../agent/src/fx_debate/models.py)、[`contracts.py`](../../agent/src/fx_debate/contracts.py)。

生产差距则很具体：

1. Context 固定 `provider_priority=["LSEG"]`，只支持 4H/1D，宏观 24 条、新闻 20 条；没有数据集级 SLA、版本策略或跨源质检（[`context.py`](../../agent/src/fx_debate/context.py)）。
2. 行情 `available_time` 以 bar time 近似；4H 聚合未拒绝未闭合/缺小时的 bucket；历史 `as_of` 仍先请求“latest price”，不保证能取到历史时点快照（[`analytics.py`](../../agent/src/fx_debate/analytics.py)、[`fx_debate_tools.py`](../../agent/src/tools/fx_debate_tools.py)）。
3. 宏观把 `release_time` 同时当 observation/available time，新闻把 `publish_time` 同时当两者；尚未表达参考期、初值/修订值、首次发布时间、入库时间、预测共识的快照时刻（[`fx_debate_content_tools.py`](../../agent/src/tools/fx_debate_content_tools.py)）。
4. `complete` 目前只要求调用三类 Tool，并不检查各币种/因子覆盖、证据是否真正支持 claim、陈旧度、来源独立性或置信度校准（[`validate_fx_output_tool.py`](../../agent/src/tools/validate_fx_output_tool.py)）。
5. Bull/Bear 的角色约束只在文字中；契约没有强制角色—方向一致，也没有要求最强反证、机制、催化剂、发生窗口和可机器判定的失效条件。

因此，当前系统证明了“链路可跑和引用可回查”，还未证明“信息在当时可知、论证有效或有预测增益”。

### 2.1 数据库导出实表校准（`db_export_0802.xlsx`）

数据库负责人提供的 2026-08-02 导出来自 `test_db`，包含 9 张表；以下只审计与前三 Agent 直接相关的四张业务表。原始工作簿含 LSEG 新闻正文，不应直接提交仓库；本文只记录结构和质量结论。

| 表 | 导出样例 | 可直接复用 | 生产阻塞点 |
| --- | --- | --- | --- |
| `latest_prices` | 15 行、15 个 `source_identifier`，单一快照时点；13/15 有 bid/ask | bid/ask/mid、供应商标识、报价时间和入库更新时间 | 无 `instrument_id`、外键、业务唯一约束或历史快照；字段为 `last/mid`，Reader 却读取 `last_price/mid_price`；存在 bid/ask 缺失和零价格，尚无质量约束 |
| `macro_observations` | 94 行、71 个 metric、12 个国家；时间覆盖约一个月 | metric、国家、单位、实际值、来源和供应商标识；政策率与国债收益率已开始进入同表 | `previous_value/forecast_value/revised_value` 全为空；23 行缺 `instrument_id`；`release_time` 同时出现月末零点和采集时刻，不能确认是真实首次发布时间；无参考期、vintage、共识快照时间或发布阶段 |
| `market_bars` | 127 行、6 个 FX RIC、仅 22 个交易日且全部为日线 | OHLC、bar time、来源和唯一键 `(source_identifier, bar_time, frequency, source)` | 无 `instrument_id`；只有 daily、每品种约 21—22 根，达不到当前 50 根指标门槛，更没有 4H；字段为 `date`，Reader 读取 `bar_date`；无 bar 起止/闭合/完整性标记；`updated_at` 是批量回填时间 |
| `news_articles` | 76 行；53 行有正文、7 行有 sentiment、0 行有 summary | article ID、发布时间、标题、原文、主题标签和入库更新时间 | Reader 读取的 `url/relevance_score` 不存在；`summary` 全空；`language` 全为 `en`，但标题/正文实际多语言；相同事件存在多语种/版本稿；导出未包含 `news_instrument_link`，无法核验货币对关联 |

另有 `cb_events` 表和相应 catalog 设计，但当前为 0 行，所以尚不能支撑事件风险状态。导出也未包含 Reader SQL 依赖的 `instrument_metric_link`、`metric_catalog`、`news_instrument_link`。这不证明数据库中不存在这些表，但在接口验收前必须一并导出约束和样例。

最优先事项因此不是继续扩 Prompt，而是冻结 **DB Contract v1**。当前代码与导出至少存在以下硬不兼容：

```text
latest_prices: last/mid                 != Reader 的 last_price/mid_price
market_bars:   date、无 instrument_id   != Reader 的 bar_date、instrument_id
news_articles: 无 url/relevance_score   != Reader SELECT 的对应列
business rows: 多表无外键               != Reader 假设的 instrument_id 关系路径
```

建议数据库团队与 Agent 团队共同交付：版本化 DDL、四条 Reader 契约查询的 golden result、字段语义字典、样例 fixture 和 schema compatibility test。可选择数据库迁移到 Reader 契约，或在 Reader 增加明确的 v1 adapter；不应靠 `COALESCE` 或隐式猜列名长期兼容两套结构。

四表之上建议增加而不是塞入 LLM 的确定性层：

1. `raw_*` 保留供应商原始事件和响应哈希，append-only；`normalized_*` 做标准代码、单位、语言和时间规范化。
2. `macro_release_vintage` 分离 `reference_period`、`scheduled_at`、`released_at`、`ingested_at`、`consensus_as_of`、`release_stage` 和 revision。
3. `market_bars` 增加 `instrument_id`、`bar_start/end`、`is_closed`、`is_complete`、时区/交易日历；EURUSD 至少补足 400 天日线与可验证的 1H，再确定性聚合 4H。
4. `news_story_cluster` 将同一事件的快讯、更新稿和翻译稿聚类；单独保存检测语言、pair/entity 关联、相关度模型版本和摘要版本。
5. `latest_prices` 只做当前状态；另建 append-only quote snapshot/history 支持历史 `as_of` 回放，并增加 `bid <= mid <= ask`、非零、陈旧度和异常 spread 检查。

## 3. 一手研究事实及其设计含义

### 3.1 数据不是只有 observation time

- FRED/ALFRED 的 real-time period 表示某事实在何时已知，API 可按 `realtime_start/end`、`vintage_dates` 读取过去某时点看到的值，也可只取 initial release（[`series/observations`](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)、[`Real-Time Periods`](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)）。**含义：**宏观 evidence 必须保存 vintage；“今天下载的 2024 GDP”不能用于模拟 2024 年当时的 Agent。
- ECB Data Portal 的 SDMX API 支持 `updatedAfter` 获取变更，并可用 `includeHistory=true` 读取旧版本（[ECB API Data](https://data.ecb.europa.eu/help/api/data)）。相反，Eurostat 官方 API 明确说明数据库只有最新版本、没有历史版本记录（[Eurostat API Introduction](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction)）。**含义：**ECB 可回采历史版本；Eurostat 必须从上线日起自行保存原始响应、检索时间和哈希，既有历史则另找合规 vintage 源。
- 欧元区 HICP 月末先发布 flash estimate，月中再发完整数据，且允许修订（[Eurostat HICP data information](https://ec.europa.eu/eurostat/web/hicp/information-data)）；BEA 的 GDP 通常经历 advance 和后续估计，并会因新增源数据修订（[BEA release descriptions](https://www.bea.gov/about/email_descriptions.htm)）。**含义：**`actual`、`previous_as_known_then`、`revised` 必须是不同字段/事件，不能覆盖同一行。
- 经济日历会改变。BEA 曾正式改期或取消一版 GDP 估计（[BEA schedule update](https://www.bea.gov/index.php/news/blog/2025-12-10/economic-release-schedule-updates)），Eurostat 日历使用 CET/CEST（[Eurostat release calendar](https://ec.europa.eu/eurostat/en/news/release-calendar)）。**含义：**既要存“计划发布时间”，也要存“实际发布时间”和时区；事件临近状态不能从今天的日历反推。

### 3.2 FX 需要相对变量、预期与市场状态

- 宏观公告对 FX 的影响来自 `actual - expectation` 的 surprise，并呈现时点和符号不对称，而不仅是指标水平（Andersen 等原始论文，[NBER w8959](https://www.nber.org/papers/w8959)）。高频货币政策 surprise 也会影响汇率（Faust 等，[NBER w9660](https://www.nber.org/papers/w9660)）。**含义：**Agent 的核心输入应包含发布前冻结的共识、首次发布值、标准化 surprise、公告后窗口收益；不能仅比较“本月 CPI 高低”。
- 当前利差不是充分解释。BIS 研究将名义汇率分解为预期未来中性实际利差、经济周期和 PPP 因素，也指出期限溢价可能经汇率传导（[BIS WP 732](https://www.bis.org/publ/work732.htm)、[BIS WP 971](https://www.bis.org/publ/work971.htm)）。**含义：**优先构造 base-vs-quote 的 OIS/政策路径、实际利率和曲线变化，而不是只读两国政策利率。
- 交易流包含宏观模型遗漏的信息；早期原始研究发现 order flow 对日度汇率有显著解释/预测力，但具体历史样本结果不能直接外推（Evans & Lyons，[NBER w7317](https://www.nber.org/papers/w7317)）。**含义：**若未来能合规获得流、深度或价差数据，应作为独立微观结构域；当前没有时必须显式记为缺失，不能由新闻情绪替代。
- Carry 暴露于全球 FX 波动风险，高息货币在意外高波动时可能表现较差（Menkhoff 等原始论文，[Journal of Finance DOI](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2012.01728.x)）；跨资产研究也记录了货币期货中的时间序列动量，但后续文献存在争论（原始论文，[Time Series Momentum](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf)）。**含义：**carry、trend 只能是条件信号，必须与波动 regime、成本和样本外结果联动，不能作为 Prompt 中的永真规则。
- BIS 的 NEER 是双边汇率的贸易加权几何平均，REER 再调整相对消费价格；月度与日度更新频率不同（[BIS EER overview](https://data.bis.org/topics/EER)）。IMF EBA 同时使用经常账户、REER 和外部可持续性框架，并强调模型与工作人员判断结合（[IMF EBA 2022 methodology](https://www.elibrary.imf.org/view/journals/001/2023/047/article-A001-en.xml)）。**含义：**REER/外部平衡适合 1—3 月估值锚，不应驱动 4H 入场。

### 3.3 仓位、期权和政策文本有各自时间语义

- CFTC TFF 将金融期货持仓分为 Dealer、Asset Manager、Leveraged Funds、Other Reportables 和 Non-reportables（[TFF dataset/API](https://publicreporting.cftc.gov/Commitments-of-Traders/TFF-Futures-Only/gpe5-46if)）；COT 通常周五 15:30 ET 发布，但内容是前一周二仓位，节假日可延迟（[CFTC release schedule](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm)）。分类还可能因 Form 40 信息变化而重分类（[CFTC COT FAQ](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm)）。**含义：**必须分别记录 `position_as_of` 与 `published_at`；COT 是滞后拥挤度代理，不是实时订单流，也不应把类别变化全解释为交易。
- CME 的 volume 是当日成交合约数，OI 是尚未平仓合约数；结算页通常显示前一交易日 OI，preliminary settlement 可能在 final 前变化（[CME V/OI definitions](https://www.cmegroup.com/market-data/volume-open-interest/about.html)、[CME settlements](https://www.cmegroup.com/trading/about-settlements.html)）。CVOL 用期权价格估计约 30 天前瞻隐含波动，并提供 UpVar/DownVar/Skew 等指标（[CVOL FAQ](https://www.cmegroup.com/market-data/cme-group-benchmark-administration/cme-group-volatility-indexes-faq.html)、[正式方法论](https://www.cmegroup.com/market-data/cme-group-benchmark-administration/files/cvol-methodology.pdf)）。**含义：**将 realized/implied vol、skew 和拥挤度分开；接入前确认数据许可和重分发边界。
- FOMC 每年通常八次例会，会议纪要一般在决议三周后发布，带 `*` 的会议有 SEP（[FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)）；SEP 每年四次，包含增长、失业、通胀与合意政策率分布（[Fed SEP FAQ](https://www.federalreserve.gov/faqs/summary-economic-projections-sep.htm)）。ECB 决议和记者会有明确发布时间，staff projections 每年四次且有数据 cutoff date（[ECB press conference](https://www.ecb.europa.eu/press/press_conference/html/index.so.html)、[ECB projections](https://www.ecb.europa.eu/press/projections/html/index.en.html)）。**含义：**政策声明、预测、纪要必须是不同事件；使用文档发布日期和其内部 cutoff，不能把纪要当作决议当日已知。

## 4. Evidence Factory 目标设计

### 4.1 EvidenceItem 2.0

每个原始或派生事实至少应包含：

```text
identity: evidence_id, context_id, dataset_id, series_id, source_uri
time: reference_period, observed_at, scheduled_release_at, released_at,
      ingested_at, valid_from, valid_to, market_session, timezone
version: vintage_id, release_stage, revision_number, supersedes_id
value: value, unit, seasonal_adjustment, frequency, expected, previous_as_known,
       surprise_raw, surprise_z
lineage: source_record_ids, transform_id/version, input_evidence_ids, content_hash
quality: source_tier, freshness_policy_id, completeness, anomaly_flags,
         license_class, quality_status
```

其中 `available_time = max(released_at, ingested_at, vendor_entitlement_time)`；若任一项未知，状态不得是 `fresh`。派生指标的 available time 取所有输入的最大值。`source_uri + response_hash + retrieved_at` 要能重放原始响应。新闻正文按“不可信数据”处理，任何其中的指令不得进入 Tool 控制流；NIST 的 GenAI Profile 将治理、内容溯源、部署前测试和事件披露列为重点（[NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)）。

### 4.2 一次运行的标准证据包

Evidence Factory 在 Agent 启动前产生 `evidence_bundle.json`，三个 Agent 只查询该冻结包：

| 域 | 必需内容 | 生产规则 |
| --- | --- | --- |
| Spot/技术 | bid/ask/mid、闭合 1H/4H/1D bar、spread、缺口、收益、ATR、realized vol、趋势/动量/区间 | 4H 只收 `bucket_end <= as_of` 且小时完整的 bucket；指标由版本化代码计算 |
| 相对利率 | 两地政策率、OIS/期货隐含路径、2Y/10Y 与实际利率/期限溢价代理 | 全部计算 `base - quote`，保留曲线快照和日变/周变 |
| 宏观 surprise | 通胀、增长/景气、就业/工资、外部平衡；actual、发布前 consensus、首次前值和修订 | surprise 用历史“首次发布 surprise”滚动标准差标准化；共识快照必须早于发布 |
| 政策文本 | 声明、发布会、SEP/ECB projection、纪要 | 官方原文为最高优先；文本抽取保留段落定位、发布时间和 cutoff |
| 定位/期权 | TFF 各类净仓位及历史分位、volume/OI、CVOL/ATM/skew | 清楚显示报告滞后；不可用时标记缺失，不回填 |
| 估值/外部 | BIS NEER/REER、经常账户/贸易条件 | 仅赋予中期相关性，不参与 4H 确定性触发 |
| 事件风险 | 未来 24h/72h/研究期内官方日历、节假日、数据黑窗 | 保存当时版本；高风险事件前后单列 regime |

建议不再要求三个 Agent“各自查询三类 Tool”。数据独立性由冻结包保障，**推理独立性**由上下文隔离保障。Tool 层提供 `get_fx_evidence_manifest`、`get_relative_macro_scorecard`、`get_technical_regime`、`get_event_risk` 和按 ID 回查；避免 LLM 自选 20 条最新新闻造成不同样本。

## 5. 三个 Agent 的具体设计

### 5.1 Pair Bull：上涨假设检察官

**职责：**回答“在当前信息集下，哪些可观测机制能使 canonical pair 在指定 horizon 上涨？”不是无条件喊多，也不负责最终下单/仓位。

**必须执行：**

1. 从六类驱动中选最多三条独立主链：政策路径、增长/通胀 surprise、资本/仓位、风险 regime、估值、价格行为。
2. 每条链写成 `事实 → 相对预期变化 → 资金/定价传导 → 方向 → 预计生效窗口`；事实、推断、假设分字段。
3. 至少给出一个宏观催化剂和一个市场确认；仅“价格在 EMA 上”不能构成完整宏观 claim。
4. 主动列出最强两项反证，并说明何种观测会让上涨假设失效；不能用同一 evidence ID 包装成多条独立 claim。
5. 若没有跨域确认，输出 `hypothesis_status=weak/insufficient`，而不是降低文字措辞后仍给 long。

**输出建议：**`HypothesisArgumentV2`，包含 `hypothesis_direction=up`、typed claims、causal graph、catalysts、confirmations、counter_case、machine_readable_invalidation、coverage、uncalibrated_score、missing_data。前端 Agent 只给“研究假设/条件价位”，删除风险百分比和伪精确仓位。

### 5.2 Pair Bear：完全镜像的下跌假设检察官

Bear 使用与 Bull **相同字段、相同 token/tool 预算、相同来源和相同最低证据覆盖**，唯一差异是 `hypothesis_direction=down`。这避免“Bull 写增长、Bear 只找风险新闻”的结构性偏差。

额外校验：Bull/Bear 都必须引用至少一条对己方不利的 evidence；若两者核心 claims 引用相同事实却给出相反机制，输出 `interpretation_conflict` 供 Risk/Judge 审核；若某一方只能依靠陈旧仓位或单篇新闻，自动降为 incomplete。Pair Agent 的价值是强制 falsification，而不是用两次采样制造“多数票”。

### 5.3 Macro & Technical：中立状态估计器

该 Agent 不应再像第三个辩手自由写长文，而应解释两个确定性 scorecard：

**相对宏观块：**

- `policy_path`: OIS 路径、2Y/10Y、实际利差及变化；
- `inflation`: 核心/服务/工资的首次发布 surprise 与趋势；
- `growth_labor`: PMI/产出/GDP/就业 surprise diffusion；
- `external_valuation`: REER、经常账户、贸易条件（中期）；
- `positioning_risk`: TFF 分位、隐含波动/skew、流动性；
- `event_state`: 政策/数据事件前、刚发布、正常期。

每块输出 base score、quote score、difference、可靠度、最新可得时间、支持/反对 evidence IDs。分数和 rolling z-score 由代码计算，LLM 只解释非线性：例如高通胀究竟代表更鹰派利率预期，还是滞胀/风险溢价。

**技术块：**

- 1D 定义主 regime，4H 定义战术状态；
- trend、momentum、realized vol、range/breakout、spread/liquidity 分开；
- 关键位必须来自确定性 swing/区间/ATR 算法并有 transform version；
- 明确 `aligned / diverging / transition / indeterminate`，不把多个相关指标当多份独立证据。

**cross-confirmation：**只输出四种关系：macro 与 technical 同向、宏观领先但价格未确认、价格先行但宏观未确认、冲突。出现事件临近、异常价差、不完整 bar 或数据源冲突时，状态优先为 indeterminate。

## 6. 契约、置信度与校验

把 `AgentArgument 1.0` 升为版本化 V2，同时保留兼容适配器。新增硬校验：

- pair role 与 hypothesis direction 一致；每个 complete argument 至少覆盖“宏观/政策 + 市场确认”，但不强迫新闻一定存在；
- Claim 的 evidence 必须满足可得时点、数据集 freshness policy 和 horizon relevance；
- 同源转述不算独立确认，派生指标共享同一组 bar 时标为同一 evidence family；
- `invalidation` 必须结构化为 `{metric, operator, threshold, valid_until, evidence_family}`；
- 一个 claim 的原始事实可由代码查，但“证据是否蕴含 statement”需在金标集上人工/独立模型复核，不能把 ID 存在性等同于语义正确；
- 任一必需域 `unknown_time`、关键 vintage 缺失、未来证据、行情异常或 license violation，强制 `insufficient_evidence`。

LLM 的 `confidence` 先视为未校准 score。生产概率由历史 out-of-sample 结果按 pair × horizon × regime 校准；样本不足时只展示 low/medium/high。概率评估使用 Brier/对数损失和 reliability diagram；后者用于检查“报 70% 的事件是否约 70% 发生”（[JMLR calibration paper](https://www.jmlr.org/papers/v23/22-0658.html)）。禁止让 Prompt 用“更有说服力”直接换算成百分比。

## 7. 候选架构与取舍

| 方案 | 优点 | 主要问题 | 结论 |
| --- | --- | --- | --- |
| A. 只增强 Prompt | 一周内可演示 | 不解决 vintage、数据覆盖、语义校验；容易把文风当能力 | 不作为生产方案 |
| B. 三 Agent 各自检索 | 自主性强 | 并发时数据/排序不同、成本高、难复现；“独立”混淆为不同信息集 | 不推荐 |
| C. 冻结包 + 镜像 Bull/Bear + 中立状态 Agent | 可复现、可比较、保留现有 DAG；可对每一层做 ablation | 前期数据工程量最大 | **推荐** |
| D. 拆 Macro 与 Technical 为两个 Agent | 专业边界更清晰 | 会改变五 Agent DAG、增加 Judge/Risk 输入，且不自动解决 PIT | 数据稳定后再评估 |
| E. 去掉 Bull/Bear，直接概率模型 | 简洁且易评分 | 失去结构化反证；现阶段数据量可能不足 | 作为 baseline，不替代 C |

## 8. 评测设计与上线门槛

### 8.1 可回放样本

每个 forecast 保存 context、原始响应哈希、数据版本、代码/Prompt/模型版本、随机参数和完整输出。使用 rolling/expanding walk-forward：只以训练窗调阈值和校准器，在下一段冻结测试；重叠 horizon 使用 block bootstrap/合适的长程方差。所有研发试验都登记，防止只汇报最好版本；金融回测的普通 holdout 在大量试验后仍可能过拟合，PBO/CSCV 是可选诊断（Bailey 等原始论文，[Journal of Computational Finance](https://scholarworks.wmich.edu/math_pubs/42/)）。

### 8.2 明确“预测正确”

对每个 horizon `h` 使用固定时点可交易 mid 的对数收益；`neutral` 区间应在测试前冻结为 round-trip spread/slippage 加波动缓冲，不能看结果后调。分别报告 4H、1D、1W、1M，不混成一个准确率。随机游走/no-change 必须是基准，因为传统汇率模型样本外很难稳定胜过它（Meese–Rogoff 结果概述，[NBER w1732](https://www.nber.org/papers/w1732)）。

### 8.3 四层指标

1. **数据层：**future leakage=0；未知 available time=0（complete 样本）；版本可重放率=100%；bar 完整率、来源延迟、修订率、跨源差异。
2. **论证层：**schema 通过率、citation precision/coverage、claim entailment、人评反证质量、重复 evidence family、事实/推断混写率、Bull/Bear 对称性。
3. **预测层：**三分类 Brier/log loss、calibration、方向准确率、MAE/RMSE（如给点预测）、按 pair/horizon/regime 的置信区间；用 Diebold–Mariano 类检验比较相同样本上的 loss，而非只看均值（原始论文，[Fed in Print](https://www.fedinprint.org/item/fedmem/38937)）。
4. **业务/运行层：**含成本后的收益仅作辅助；同时报告 turnover、最大回撤和尾部损失；延迟、失败率、token/调用成本、模型版本漂移、人工 override 和事故率。

必须做 ablation：确定性 baseline；仅 macro；仅 technical；去 news；去 positioning/options；单 Agent；Bull+Bear；三 Agent；不同模型/温度。若完整系统没有持续、统计上可解释地优于简单 baseline，应保留为研究摘要工具，而非方向决策系统。

### 8.4 分阶段门槛

- **PIT 单测门：**构造 flash/revision、COT 周二/周五、CME OI T-1、未闭合 4H bar、日历改期用例，全部拒绝未来信息。
- **历史研究门：**至少跨多个政策/波动 regime；预注册主要指标和阈值；保持最终留出期完全未触碰。
- **Shadow 门：**连续记录实时输入与事后结果，不影响交易；人工抽检 claim entailment 和事件处理。
- **有限使用门：**只作为研究辅助，显示 missing/late/conflict；禁止自动下单；模型/数据变更触发回归和再验证。

银行模型风险的一手监管指导强调“有效挑战”、上线前验证、结果分析、持续监控和第三方模型验证（[Federal Reserve 2026 Model Risk Guidance](https://www.federalreserve.gov/frrs/guidance/supervisory-guidance-on-model-risk-management.htm)）。该文件明确不覆盖生成式/Agentic AI，因此这里只把这些作为治理类比，而非声称满足监管合规。BCBS 239 也强调风险数据的准确、完整、及时与 lineage（[BCBS 239](https://www.bis.org/publ/bcbs239.htm)）。

## 9. 实施路线图

**阶段 0（2 周，先堵漏洞）：**先与数据库团队冻结 DB Contract v1，并用真实导出跑 schema compatibility test；定义 outcome 与 PIT 词汇表；给 `EvidenceItem` 加 release/ingest/vintage；拒绝未闭合 4H bar；历史 latest quote 改成 as-of 查询；建立数据集 freshness policy 与 replay fixture。

**阶段 1（3—5 周，Evidence Factory）：**EURUSD 先落地官方 Fed/ECB/Eurostat/BEA/BLS 数据与内部 LSEG 共识快照；加入政策日历、TFF；期权数据取决于许可；生成 manifest、relative scorecard 和 technical regime。

**阶段 2（2—3 周，Agent V2）：**实现镜像 Prompt、`HypothesisArgumentV2`、结构化失效条件、evidence-family 去重、coverage gate；Macro & Technical 改为解释确定性 scorecard。

**阶段 3（4—8 周，评测）：**建立 PIT 回放集、baseline、walk-forward、校准和 ablation；邀请 FX 研究/交易/风控分别标注金标集，研发者不能独立批准自己的模型。

**阶段 4（至少 8—12 周，shadow）：**运行实时 shadow，按周监控数据延迟、校准、regime 漂移和失败案例；达到预注册门槛后，才讨论扩展 GBPUSD/USDJPY 和有限研究辅助使用。

## 10. 尚未验证与需要项目方确认

1. 当前 PostgreSQL 中 LSEG `release_time` 的供应商原始语义、历史快照留存和许可范围；导出已确认样例的 `previous/forecast/revised` 全空，但不能据此判断正式数据是否可补齐。
2. 导出表与当前 Reader SQL 是否来自同一数据库版本；`get_latest_prices` 是否另有历史 as-of 表、小时 bar 是否在其他表中、数据库写入延迟如何记录。现有导出未证明这些能力。
3. CME/CVOL、OIS 曲线、共识预测和新闻内容的现有订阅/非展示使用/内部再分发权限与成本。
4. “实际使用”的决策目标：研究摘要、人工交易建议、风险对冲还是自动执行。本文按“研究辅助、无自动下单”设计；若用途改变，验证、合规和控制范围必须重做。
5. 可接受的预测 horizon、neutral 标签、成本模型和业务损失函数。它们必须由实际使用者在回测前确定，不能由研发者事后优化。

最终建议是在 EURUSD 上先把 **可得时点—版本—相对因子—可回放评测** 做成一条窄而完整的生产链。只有它通过 shadow 与基准比较后，再扩币种、扩模型或拆 Agent；否则系统只会从“简陋 Demo”变成“更流畅但不可验证的 Demo”。
