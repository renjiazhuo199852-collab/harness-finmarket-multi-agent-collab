# FX Debate 路由稳健性测试报告

**测试日期**：2026-08-18
**测试对象**：`src.fx_debate.router.route_fx_prompt` 及其后端 Session/SSE 接入
**测试目标**：验证自然语言是否能稳定分流到 `fx_debate`、`generic` 或 `clarify`，并确认 FX Debate 路由不会误落到股票或其他研究 preset。

## 1. 测试方法

路由矩阵直接调用当前确定性公开入口 `route_fx_prompt(prompt, explicit_preset=None)`。该入口只负责意图、货币对和周期解析，不调用 LLM、数据库或行情服务，因此结果可重复。

另外做了一次后端接口冒烟：

1. `GET /openapi.json`：HTTP 200。
2. `GET /swarm/presets`：HTTP 200，`fx_debate_team` 存在，`agent_count=5`，公开变量为 `target/timeframe/goal`。
3. `POST /sessions` 创建临时 Session。
4. `POST /sessions/{session_id}/messages` 发送“分析 EURUSD 未来两周走势并给出交易建议”。
5. `GET /sessions/{session_id}/events` 观察到 `run_fx_debate` 心跳、`pair_bull`、`pair_bear`、`macro_technical` Agent 事件，以及 `get_fx_evidence_manifest`、`get_fx_relative_macro_scorecard`、`get_fx_technical_regime` 等工具事件。
6. 测试运行随后通过 `POST /sessions/{session_id}/cancel` 取消，避免继续消耗外部数据和 LLM 资源。

后端 Session 是 LLM 驱动的自然语言执行入口；因此路由分类的确定性结论以矩阵测试为准，Session/SSE 冒烟只验证真实通信和执行链路。

## 2. 用例结果

测试文件：[`agent/tests/test_fx_router_matrix.py`](../../agent/tests/test_fx_router_matrix.py)

| 分组 | 类型 | 用例 | 输入摘要 | 预期 | 实际 | 结果 |
|---|---|---|---|---|---|---|
| 普通 | `fx_debate` | N01 | EURUSD，两周，交易建议 | EURUSD / P2W + 4H,1D | 同预期 | PASS |
| 普通 | `fx_debate` | N02 | lowercase `eur/usd`，英文问题 | EURUSD | 同预期 | PASS |
| 普通 | `fx_debate` | N03 | 欧元兑美元，两周，多空 | EURUSD | 同预期 | PASS |
| 普通 | `fx_debate` | N04 | GBP-USD，10 天 | GBPUSD / P10D | 同预期 | PASS |
| 普通 | `fx_debate` | N05 | ISO 三变量格式 | EURUSD | 同预期 | PASS |
| 普通 | `fx_debate` | N06 | 缺少周期 | 默认 P2W + 4H,1D | 同预期 | PASS |
| 边界 | `clarify` | B01 | 同时出现 EURUSD、GBPUSD | `FX_MULTIPLE_PAIRS` | 同预期 | PASS |
| 边界 | `clarify` | B02 | USD/人民币未区分在岸/离岸 | `FX_CNY_CNH_AMBIGUOUS` | 同预期 | PASS |
| 边界 | `clarify` | B03 | 使用 1H、1D | `FX_TIMEFRAME_UNSUPPORTED` | 同预期 | PASS |
| 边界 | `clarify` | B04 | 同时使用 4H、1H | `FX_TIMEFRAME_UNSUPPORTED` | 同预期 | PASS |
| 边界 | `clarify` | B05 | 未来一个月 | `FX_HORIZON_UNSUPPORTED` | 同预期 | PASS |
| 边界 | `clarify` | B06 | 两周和一个月冲突 | `FX_HORIZON_CONFLICT` | 同预期 | PASS |
| 边界 | `generic` | B07 | 报价并预测 | 不启动 Debate | 同预期 | PASS |
| 边界 | `clarify` | B08 | 空问题 | `FX_PROMPT_EMPTY` | 同预期 | PASS |
| 通用 | `generic` | G01 | 查询 EURUSD 当前汇率 | 不启动 Debate | 同预期 | PASS |
| 通用 | `generic` | G02 | 欧元兑换美元 | 不启动 Debate | 同预期 | PASS |
| 通用 | `generic` | G03 | AAPL 走势 | 不启动 Debate | 同预期 | PASS |
| 通用 | `generic` | G04 | BTCUSDT 走势 | 不启动 Debate | 同预期 | PASS |
| 通用 | `generic` | G05 | ETHUSD 走势 | 不启动 Debate | 同预期 | PASS |
| 通用 | `generic` | G06 | 解释美联储加息影响 | 不启动 Debate | 同预期 | PASS |
| 通用 | `generic` | G07 | 无货币对的方向问题 | 不启动 Debate | 同预期 | PASS |
| 兼容 | `fx_debate` | P01 | 显式 `fx_debate_team` | EURUSD | 同预期 | PASS |
| 兼容 | `fx_debate` | P02 | 显式旧别名 `fx_pair_debate_desk_3vars_v1` | 映射当前 FX Debate | 同预期 | PASS |

## 3. 自动化结果

```text
23 passed in 0.58s  # 新增路由验收矩阵
75 passed in 2.18s  # FX 路由及相关 preset/tool/request 回归集
```

回归集包含：

- 路由矩阵和既有 `test_fx_router.py`；
- Swarm preset 匹配，确保 FX 不落到 `equity_research_team`；
- 三变量 request adapter，包含人类可读和 ISO timeframe；
- `RunFxDebateTool` 委托与数据不可用错误码；
- Tool 注册事件 callback；
- FX Debate preset 的五 Agent、三变量契约。

## 4. 本次发现与修复

此前确定性解析会把 `BTCUSDT`、`ETHUSD` 中的子串 `USD` 误识别为美元货币代码，可能将加密问题错误带入 FX 路由。现已在 [`agent/src/tools/fx_deterministic_parser.py`](../../agent/src/tools/fx_deterministic_parser.py) 对 ASCII 货币别名增加字母数字边界匹配，G04/G05 回归用例验证通过。

## 5. 结论与剩余风险

当前路由对普通 FX Debate、常见货币对格式、默认周期、歧义货币对、非法周期、冲突周期和非 FX 请求均能按预期分流；数据源不可用时由执行工具返回稳定错误，不会静默转成股票分析。

仍需注意：`/sessions` 的自然语言执行包含 LLM 规划层，LLM 可能对复杂问题先做一次意图改写。因此，若后续要求“HTTP 层也必须先返回确定性 route 结果再启动 Agent”，建议新增只读的 route preview 接口，或在 Session planner 前强制调用同一个 `FxRouter`。本次没有新增该接口，避免改变现有 Session/SSE 协议。

## 6. 复现命令

```bash
cd harness-finmarket-multi-agent-collab
PYTHONPATH=agent .venv/bin/pytest -q agent/tests/test_fx_router_matrix.py

PYTHONPATH=agent .venv/bin/pytest -q \
  agent/tests/test_fx_router_matrix.py \
  agent/tests/test_fx_router.py \
  agent/tests/test_swarm_tool_preset_matching.py \
  agent/tests/test_fx_debate_request_adapter.py \
  agent/tests/test_run_fx_debate_tool.py \
  agent/tests/test_fx_debate_tool_registration.py \
  agent/tests/test_fx_debate_preset.py
```

## 附录 A：完整输入

以下内容是测试矩阵实际传入 `route_fx_prompt` 的完整原始字符串。

### A.1 普通 FX Debate

| 用例 | 完整输入 |
|---|---|
| N01 | `分析 EURUSD 未来两周走势并给出交易建议` |
| N02 | `analyze eur/usd next two weeks outlook and trade idea` |
| N03 | `分析欧元兑美元接下来两周的多空机会` |
| N04 | `分析 GBP-USD 未来 10 天走势并给出交易建议` |
| N05 | `分析 EURUSD，decision_horizon=P2W; analysis_timeframes=PT4H,P1D` |
| N06 | `请用五 Agent Debate 分析 EURUSD` |

### A.2 复杂边界和歧义

| 用例 | 完整输入 |
|---|---|
| B01 | `分析 EURUSD 和 GBPUSD 未来两周走势` |
| B02 | `分析 USD/人民币未来两周走势` |
| B03 | `分析 EURUSD 未来两周，使用 1H 和 1D` |
| B04 | `分析 EURUSD 未来两周，同时看 4H 和 1H` |
| B05 | `分析 EURUSD 未来一个月走势` |
| B06 | `分析 EURUSD 未来两周和未来一个月走势` |
| B07 | `分析 EURUSD 报价并预测未来两周走势` |
| B08 | 空字符串：`""` |

### A.3 普通通用路由

| 用例 | 完整输入 |
|---|---|
| G01 | `查询 EURUSD 当前汇率` |
| G02 | `把 1000 欧元兑换成美元` |
| G03 | `分析 AAPL 未来两周走势` |
| G04 | `判断 BTCUSDT 未来两周走势` |
| G05 | `分析 ETHUSD 未来两周趋势` |
| G06 | `解释美联储加息对美元的影响` |
| G07 | `分析未来两周走势并给出交易建议` |

### A.4 显式 preset 兼容

以下两条使用相同的完整输入，但分别传入 `explicit_preset`：

| 用例 | 完整输入 | `explicit_preset` |
|---|---|---|
| P01 | `分析 EURUSD` | `fx_debate_team` |
| P02 | `分析 EURUSD` | `fx_pair_debate_desk_3vars_v1` |
