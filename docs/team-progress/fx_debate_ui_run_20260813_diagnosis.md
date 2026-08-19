# FX Debate UI 运行诊断（2026-08-13）

## 结论

本次失败运行 `agent/.fx_debate_ui/jobs/fxui-d81147a50233` 的主因不是数据库连接，而是 Bull/Bear 的模型输出与 V2 契约发生形态漂移。Macro + Technical 已经通过校验；Bull/Bear 多次调用 `validate_fx_output`，最终仍未得到 `valid=true`，所以 Risk Officer 和 Judge 被正确阻断。

用户截图对应的最新运行 `fxui-11368d038b6e` 还叠加了一个服务版本问题：8898 上的旧 Python 进程没有加载兼容层，仍会把 `operator=equals`、`driver` 字符串、scorecard 的 `rates` 维度等合法旧格式直接判为 schema error。重启服务后，回放该运行的 6 次校验结果全部为 `valid=true`；旧的运行记录不会被自动改写。

## 日志证据

- `causal_chains` 使用了 `chain_id / observed_facts / transmission / window`，而不是 V2 的 `claim_id / observed_fact / transmission_mechanism / effective_window`。
- `catalysts` 使用 `description`，`strongest_countercase` 有时是单个字符串或对象。
- 失效条件使用 `operator=equals`、`description` 和 `evidence_ids`；coverage 的 `domains` 是对象而不是列表。
- `strength` 有时是 `{rating: "weak"}`；tool trace 使用 `{tool, status}` 或字符串。

这些都属于可兼容的旧/紧凑表达，不应降低证据门槛。当前契约适配层会在校验前做字段映射、方向归一化和真实 evidence ID 提取；不完整数据仍只能得到 `weak/insufficient`。

## 数据源问题

原始导出 Excel 适合做降级测试，但不是完整输入：报价较旧，日线不足 50 根、没有可用 4H，宏观 forecast 缺失，事件日历未接入。因此即使输出契约修复，正确结论也应是低可靠性的 `wait/hedge`，而不是 `supported/high`。

已生成只用于链路测试的完整合成数据：

`agent/outputs/fx-debate-synthetic/complete_eurusd.xlsx`

验证结果为：1 条报价、300 根市场 K 线、8 条 EU/US 宏观记录、3 篇新闻，四域 manifest 均为 `complete`；1D 有 60 根、4H 有 60 根，宏观有 4 个带 forecast 的信号。合成数据不代表真实市场，不得用于交易判断。

## 已落地修复

1. UI 将校验 JSON 渲染为“验证状态 + 错误表格 + evidence chips”，原始 JSON 收入折叠区，便于回溯历史事件。
2. 新增设置页，可配置 Provider、Model、Base URL、API Key、Reasoning、Excel 路径和数据库连接；密钥只写本机 `agent/.env`，响应不回显原值。
3. 设置页提供“使用完整合成数据”按钮，并正确展开 `.env` 中的 `${DEEPSEEK_BASE_URL}` 引用。
4. 增加真实失败输出形态的回放测试，确保兼容映射不绕过 evidence、4H 和 forecast 校验。
5. 服务启动时自动把上一次进程中断留下的 `queued/running` 任务标记为失败，避免页面永久停在“运行中”。

验证命令：

```bash
python agent/scripts/verify_fx_excel_source.py \
  --xlsx agent/outputs/fx-debate-synthetic/complete_eurusd.xlsx \
  --symbol EURUSD --as-of 2026-08-13T00:00:00Z
```
