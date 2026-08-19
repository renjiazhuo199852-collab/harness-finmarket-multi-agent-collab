# FX Debate 前三 Agent V2 / Tool 实现说明

## 1. 当前范围

本实现面向郭骁然负责的前三个 Agent / Tool 部分。统一的 `FxEvidenceSource`
支持数据库 Reader 和 Excel 导出两个 Adapter，不修改数据库 SQL 或表结构。

- 对外入口保持通用 FX 结构，当前可验收标的只开放 `EURUSD`。
- 数据源通过 `FX_DEBATE_DATA_SOURCE=database|excel|ai_search` 显式选择。
  `ai_search` 通过 `FX_DATA_SERVICE_URL` 调用独立数据服务；三种来源之间不自动
  静默回退，避免把数据源切换误认为证据连续。
- 研究周期支持 `4H`、`1D`，研究期限支持 1—90 天。
- 入口既接受 Planner 的 `target/timeframe/goal`，也兼容已解析的
  `ResolvedFxDebateRequest`；两种输入不能同时提交。
- 最终结果只给研究建议，不调用交易或下单接口。

## 2. 五 Agent DAG

```text
Pair Bull ───────────────┐
Pair Bear ───────────────┼─> FX Risk Officer ─> Debate Judge / FX PM
Macro & Technical ───────┘
```

前三个分析 Agent 并行执行：Bull/Bear 输出 `HypothesisArgumentV2`，中立分析员
输出 `RelativeStateV2`。Risk 通过统一 claims view 消费三份 V2 输出，Judge 同时
读取三份原始分析和 `RiskReview`。预设文件为
`agent/src/swarm/presets/fx_debate_team.yaml`。

## 3. 公开 Tool

| Tool | 职责 |
| --- | --- |
| `run_fx_debate` | 校验 Planner 请求、创建 Evidence Context、启动五 Agent、执行最终校验并渲染中文报告 |
| `get_fx_evidence_manifest` | 返回冻结 Bundle 的数据完整度、异常和缺失项 |
| `get_fx_relative_macro_scorecard` | 返回 EUR-vs-USD 相对宏观信号及证据 ID |
| `get_fx_technical_regime` | 返回确定性计算的 1D/4H 技术状态 |
| `get_fx_story_clusters` | 返回规则去重后的新闻事件簇，不复制长正文 |
| `get_fx_evidence_by_ids` | 在本次 Evidence Context 内按 ID 回查完整证据 |
| `validate_fx_output` | 校验 V1/V2 前置输出、RiskReview、FinalDecision 的结构、证据和风控约束 |

`run_fx_debate` 在启动 Agent 前构建一次 Evidence Bundle；Agent Tool 只能读取该
Bundle，不会再次查询 Excel 或数据库。Bundle 存在 runtime-owned trusted context，
被 Tool 读取后将证据登记在：

```text
agent/.swarm/runs/<run_id>/fx_debate/contexts/<context_id>/
└── evidence/
```

因此并行的 Bull、Bear、Macro & Technical 共享同一批 Evidence Item。

## 4. 本地环境

当前仓库使用独立且被 Git 忽略的 `agent/.env`，不读取或链接
`Vibe-Trading/agent/.env`。Python 虚拟环境仍可复用：

```bash
../Vibe-Trading/.venv/bin/python
```

Excel 本地 Demo 配置：

```env
FX_DEBATE_DATA_SOURCE=excel
FX_DEBATE_EXCEL_PATH=/absolute/path/db_export_0802.xlsx
```

数据库就绪后改为 `FX_DEBATE_DATA_SOURCE=database`，并按
`docs/team-progress/market_data_tools_team_guide.md` 配置 Reader。

需要使用上游数据服务时，明确选择：

```env
FX_DEBATE_DATA_SOURCE=ai_search
FX_DATA_SERVICE_URL=http://127.0.0.1:8787
```

报价超过 Evidence Context `as_of` 前 24 小时会标记为 `stale`，不会被呈现为
新鲜报价；缺少小时数据的 4H bucket 也会被丢弃并降级为数据不足。

## 5. 调用示例

```python
import json

from src.tools.run_fx_debate_tool import RunFxDebateTool

result = json.loads(
    RunFxDebateTool().execute(
        resolved_request={
            "status": "resolved",
            "asset_class": "fx",
            "instrument_type": "spot",
            "pair_class": "major",
            "canonical_symbol": "EURUSD",
            "display_symbol": "EUR/USD",
            "base_currency": "EUR",
            "quote_currency": "USD",
            "requested_base_currency": "EUR",
            "requested_quote_currency": "USD",
            "inverted": False,
            "horizon": "2 weeks",
            "timeframe": "4H/1D",
        },
        run_options={
            "request_id": "req_demo_001",
            "risk_profile": "balanced",
            "language": "zh-CN",
        },
    )
)
```

只有 `result["status"] == "completed"` 时，`result["decision"]` 才是通过完整
结构、证据存在性、Context 一致性和 Risk 约束校验的 `FinalDecision`。

## 6. 测试

```bash
../Vibe-Trading/.venv/bin/python -m pytest \
  agent/tests/test_fx_debate_context.py \
  agent/tests/test_fx_evidence_store.py \
  agent/tests/test_fx_market_evidence_tools.py \
  agent/tests/test_fx_macro_news_evidence_tools.py \
  agent/tests/test_fx_evidence_factory_v2.py \
  agent/tests/test_fx_bundle_tools.py \
  agent/tests/test_validate_fx_output_tool.py \
  agent/tests/test_validate_fx_output_v2.py \
  agent/tests/test_fx_debate_preset.py \
  agent/tests/test_fx_debate_runtime_context.py \
  agent/tests/test_fx_debate_tool_registration.py \
  agent/tests/test_run_fx_debate_tool.py \
  agent/tests/test_fx_debate_test_server.py -q
```

只读检查数据库导出 Excel：

```bash
../Vibe-Trading/.venv/bin/python agent/scripts/verify_fx_excel_source.py \
  --xlsx /absolute/path/db_export_0802.xlsx \
  --symbol EURUSD \
  --as-of 2026-08-02T00:00:00Z
```

韦庆檑 SDK 回归：

```bash
../Vibe-Trading/.venv/bin/python -m pytest \
  agent/tests/test_market_database.py \
  agent/tests/test_market_data_reader.py \
  agent/tests/test_internal_market_data_tools.py -q
```

## 7. 独立测试前端

测试页和原项目 Web 前端彼此独立，只读取当前仓库的 `agent/.env`。在仓库根目录
启动：

```bash
../Vibe-Trading/.venv/bin/python -m uvicorn \
  --app-dir agent \
  fx_debate_test_server:app \
  --host 127.0.0.1 \
  --port 8898
```

浏览器打开 <http://127.0.0.1:8898>。页面只在以下两项同时就绪时开放“启动真实
五 Agent Debate”按钮：

1. Excel 路径有效，或内部 PostgreSQL 配置完整；
2. 已设置 LLM provider、model 和对应 API key。

修改 `agent/.env` 后需要重启服务。点击运行会读取真实数据并产生 LLM 调用；
服务一次只接受一个运行任务，结果仅保存在当前服务进程内，完整 Swarm 运行产物
仍写入 `agent/.swarm/runs/`。

测试页是调试控制台，不是业务展示页。运行期间会增量显示：

- 五个 Agent 的完整 system/user 输入、流式生成状态和最终输出；
- Tool 的名称、脱敏输入、结构化输出、状态及耗时；
- `MarketDataReader` SDK 方法的输入、返回数量和前五条结果预览（database 模式）；
- PostgreSQL 参数化 SQL、绑定参数、字段、行数和前五条结果预览（database 模式）；
- 当前正在工作的 Agent、Tool、SDK 和数据查询。

页面通过 `GET /api/runs/{job_id}/events?after={sequence}` 增量读取事件。事件进入
浏览器前会再次按仓库统一规则脱敏，API key、数据库密码、token、authorization
及个人账户标识不会显示。
