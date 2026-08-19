# FX Debate Run 回溯诊断

## 结论

本次 `swarm-20260812-085341-0c0aa57d` 的 `wait` 方向与数据不足相符，但用户看到的 Judge JSON **不是合法最终结果**。主故障是 Risk/Judge 的 `upstream_summaries` 已存在于 runtime，却没有进入实际 LLM system prompt。Risk 因而误判“三份上游输出缺失”，Judge 再继承该错误。外层 `_validate_all_outputs()` 重放会在 Bear 报告处触发 `JSONDecodeError`，所以通用 `worker_completed` 不能视为 Debate 成功。

## Run 概况

- 时间：2026-08-12 08:53:41Z–09:09:06Z（约 15 分 25 秒）
- 模型：`gpt-5.6-terra`，Reasoning=`none`
- Context：`fxctx-f2c1647888b34c07`
- 数据源：Excel
- Swarm 状态：`completed`；FX 公共输出契约：失败
- Provider 波动：Bull 首次执行遇到 HTTP 524，Bear 首次执行遇到 HTTP 502，随后均整任务重试

## 数据源诊断

| 域 | 本轮实际可用 | 结论 | 问题 |
|---|---:|---|---|
| Quote | 1 条 EURUSD | 字段与 spread 正常 | 时间为 2026-07-31 19:53Z，较 as-of 滞后约 11.5 天，却被标为 `fresh` |
| Market bars | 21 根 1D、0 根 4H | `indeterminate` | 1D/4H 均不足 50 根，无任何 technical evidence ID |
| Macro | 28 条 EU/US | 仅利率为 `quote_supported` | 28/28 forecast 缺失；growth/labor/inflation 全部 unknown |
| News | 33 个 story cluster | 可作上下文 | 新闻也停留在 7 月 31 日附近；无事件日历，不能当作确定性催化剂 |
| Event | 0 | `unknown` | `event_calendar_not_connected` |

Factory 当前只检查报价非零和 `bid <= mid <= ask`，没有按 as-of 计算 quote/news/macro 的年龄，因此“字段有效”被错误呈现成“新鲜”。原工作簿还在四张数据表下附带约 21 行 schema/索引说明；目前 Adapter 因遇到空行停止而未读入，但这种混合导出格式较脆弱。

## 五 Agent 全流程

### 1. Relative Macro & Technical

- 输入：可信 request/context；无上游依赖。
- Tool：加载两个 Skill；读取 manifest、macro scorecard、technical regime。
- 验证：前 4 次分别因 schema、角色枚举和伪造 evidence ID 失败，第 5 次通过。
- 输出：合法 `RelativeStateV2`；宏观、技术和交叉确认均为 `indeterminate`，reliability=`low`。
- 质量问题：用利率 evidence ID 支撑“技术数据缺失”和“事件日历缺失”，结构合法但语义证据不匹配。

### 2. Pair Bull

- 首次执行在 iteration 5 遇到 HTTP 524，完整重试。
- Tool：读取四类 bundle 数据并回查 5 个 evidence ID。
- 验证：经过 5 次 schema 修订后通过。
- 输出：合法 `HypothesisArgumentV2`，status=`insufficient`、causal_chains 为空。
- 质量问题：`tool_calls=[]` 丢失审计轨迹；用宏观利率 evidence ID 支撑“没有技术确认”。

### 3. Pair Bear

- 首次执行在 iteration 6 遇到 HTTP 502，完整重试。
- Tool：读取 bundle，但没有调用 `get_fx_evidence_by_ids`。
- 验证：经过 6 次修订后 Tool 返回 valid=true。
- 输出：内容上为 weak 下行假设；报告只写了人类可读 Markdown，**没有 Machine-readable V2 JSON block**。
- 后果：外层 `_extract_json()` 无法解析，整个 FX Tool 最终失败。

### 4. FX Risk Officer

- runtime 中确实存在 Bull、Bear、Macro 三份 `upstream_summaries`（合计约 5,945 字符）。
- 实际 system prompt 不含 `Upstream Context`、Context ID 或任何上游 claim。原因是 `build_worker_prompt()` 只替换 `{upstream_context}`，而当前 Agent prompt 没有该占位符。
- Risk 误调用 `get_fx_evidence_by_ids([""])`，随后 5 次验证全部失败。
- 最终写出的对象是自造 `RiskReviewV1`，不符合现有 `RiskReview` 契约，却仍被通用 Worker 标为 completed。

### 5. Debate Judge

- runtime 中存在四份上游 summary（约 6,951 字符），同样未注入实际 prompt。
- Judge 将 `v2_technical`、`v2_macro`、`v2_hypothesis`、`risk_review` 当成 evidence ID 回查，全部 not found。
- 未调用 `validate_fx_output`。
- 输出字段为 `symbol/rationale/entry_plan/risk_limits`，不符合 `FinalDecision` 契约。
- `worker_completed` 只表示写出了 `report.md`，不表示验证通过。

## 根因排序

1. **P0：上游上下文注入断路。** Risk/Judge prompt 缺少 `{upstream_context}`。
2. **P0：完成态未绑定验证态。** `write_file` 后即能 completed，Risk/Judge 可绕过失败验证。
3. **P0：下游消费 Markdown 而非 runtime 解析后的结构对象。** Bear 少一个 JSON block 即让全链路失败。
4. **P1：空 Skill 白名单被解释为“展示全部 Skill”。** Risk/Judge system prompt 分别约 22K 字符，而真正需要的角色说明很短。
5. **P1：数据严重不足且 freshness 未检查。** 即使修复编排，当前 Excel 仍应输出 wait/hedge。
6. **P2：Reasoning=none 与未内嵌完整 schema 造成大量字段试错。** 它放大失败概率，但不是上下文丢失的根因。

## 建议修复顺序

1. 在 Risk/Judge system prompt 显式加入 `{upstream_context}`，并为 preset 增加“有 input_from 就必须出现在 build_worker_prompt”的测试。
2. runtime 在进入下一层前解析并验证前三份 V2，传递结构化 JSON，而不是让下游从 Markdown 自行提取。
3. Worker 记录本轮最后一次 `validate_fx_output.valid`；FX Agent 只有 valid=true 且 report 中存在同一 JSON 才能 completed。
4. `skills=[]` 返回 `(no matching skills)`，不要向无 `load_skill` 的 Agent 注入全库说明。
5. 在 Evidence Factory 增加按域 freshness 阈值；至少将 11.5 天旧的 spot quote 标为 stale。
6. 为 RiskReview/FinalDecision 在 prompt 中列出完整字段模板；若仍使用 Reasoning=none，优先让 runtime 预填 schema skeleton。

## 完整 Synthetic 数据源

已生成 `agent/outputs/fx-debate-diagnosis-20260812/fx_debate_complete_synthetic.xlsx`：1 条正常报价、120 根日线、480 根小时线、48 条带 forecast 的 EU/US 宏观记录、25 篇合成新闻。Evidence Factory 验证结果为 overall/quote/market/macro/news 全部 `complete`；1D 与 4H 均为 `bullish`，分别有 120 与 100 根有效聚合 bar。该文件只用于链路测试，内容均标记为 `SYNTHETIC`，不能用于真实交易判断。
