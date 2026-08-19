# FX Debate 工作区

本文件是当前 FX Debate 版本的开发与启动说明，面向需要本地运行、联调和演示的开发者。

## 本次修改

本版本围绕“自然语言问题 -> 外汇路由 -> 五 Agent Debate -> 证据与报告”完成了以下改造：

- 新增独立前端 `frontend-fx-debate/`，提供 FX Debate 专用对话和可视化工作区。
- 新增 Vibe-compatible Session/SSE 工作区：对话、协作画布、数据概览、流程日志、最终报告和 API 设置页。
- 新增确定性 FX Router。可识别 EURUSD、EUR/USD、EUR-USD 等格式，并解析研究期限和 `4H/1D` 周期；歧义请求会返回澄清，不会误启动其他资产分析。
- 保留当前正式 preset `fx_debate_team`，兼容旧名称 `fx_pair_debate_desk_3vars_v1`，不增加旧版 YAML preset。
- 当前五个角色为：
  `Pair Bull`、`Pair Bear`、`Relative Macro & Technical Analyst`、`FX Risk Officer`、`Debate Judge / FX PM`。
  前三者并行研究，随后执行风险复核和交易经理综合判断。
- `run_fx_debate` 在 Agent 启动前创建本次运行专属 Evidence Context/Bundle；行情、宏观、新闻和技术证据通过受控 Tool 回查，最终报告经过确定性结构和风险校验。
- FX 数据统一通过独立 AI Search 服务获取，不在 Agent 侧切换或静默回退到其他数据源。
- 前端设置页支持通用 OpenAI-compatible 供应商、模型、后端地址和数据服务地址配置，并可从浏览器直接测试后端和供应商的 `/models` 接口。
- 增加数据稳健性约束：报价时效、4H 数据完整性、证据缺失和异常状态会进入结果质量，而不会被前端补造成“新鲜行情”。本系统只生成研究建议，不执行下单。

## 相较 Vibe Trading 的新增边界

本项目是在 Vibe Trading 的 Session、SSE、AgentLoop、Tool Registry 和 Swarm Runtime
基础上增加 FX Debate 领域能力。下面的内容是增量，不替换 Vibe Trading 原有的通用能力：

| 能力 | Vibe Trading 基础能力 | 本项目新增或扩展 |
| --- | --- | --- |
| 用户问题路由 | 通用 Agent 根据问题选择工具或 Swarm | 确定性 FX Router，先识别货币对、期限和周期，再决定进入 `fx_debate_team`、通用路由或澄清 |
| Swarm | 通用 preset 和 worker 调度 | 固定五角色 FX Debate DAG：多头、空头、宏观技术、交易经理和风险官 |
| 数据使用 | 可使用已有行情工具和数据源 | 运行前创建独立 Evidence Context，数据先冻结成 Evidence Bundle，再允许 Agent 按 evidence id 回查 |
| 数据检索 | 工具调用通常由 Agent 直接组织参数 | 新增独立 AI Search 服务和 `FxDataQueryAgent`，将自然语言查询转换为受控数据服务请求 |
| 数据库 | Vibe Trading 本身的本地存储和已有市场数据能力 | 独立 PostgreSQL `source` / `ai_search` 数据库，只读访问、目录校验、向量/模糊检索和安全适配器 |
| 前端 | Vibe Trading 通用对话和运行页面 | `frontend-fx-debate` 增加协作画布、数据概览、流程日志和 FX 结构化报告 |
| 输出 | 通用 Agent 文本或 Swarm 报告 | 方向、概率、入场、止损、止盈、持仓周期、失效条件和证据引用的结构化校验 |

FX Debate 不会把上游旧版五 Agent YAML 复制进来。
`fx_pair_debate_desk_3vars_v1` 只保留为兼容别名，实际运行 preset 始终是
`fx_debate_team`。

## AI Search 独立数据服务

### 设计原则

AI Search 是数据端同事交付的独立服务目录：
`external/market-data-tools/`。FX Debate Agent 不导入其中的 Python 模块，也不直接
拼接 SQL；本地运行时两边通过 MCP stdio 连接。这样数据端可以独立替换查询解析、
Embedding、模糊匹配或数据库适配器，而不改变五个 Debate Agent。

```text
FX Debate / 顶层 Agent
        │
        │ MCP stdio: 自然语言 query + 过滤条件
        ▼
external/market-data-tools MCP Server
        │
        ├─ query parser / dataset candidate selector
        ├─ unified_search（优先入口）
        ├─ instrument / dataset / field catalog 校验
        ├─ 安全业务适配器
        └─ 服务端只读存储访问
                 ├─ source.*
                 └─ ai_search.*（检索文档与 Embedding）
```

服务端只返回业务结果和允许暴露的来源元数据，不返回 SQL、候选文档、模型原始输出或
数据库连接信息。Agent 侧只依赖稳定的 `status`、`data`、`meta` 和错误码。

### AI Search 数据库结构

数据库不是 FX Debate 的本地数据库，而是独立数据服务的后端。正式快照包含：

- `source` Schema：`instrument_master`、`instrument_identifier`、`dataset_catalog`、
  `dataset_field_catalog`、`latest_prices`、`market_bars`、`macro_observations`、
  `news_articles`、`cb_events` 等正式业务和目录表。
- `ai_search` Schema：`instrument_search_documents`、`dataset_search_documents`、
  `news_search_documents` 三类检索文档表。
- 检索文档包含与当前 Embedding 模型匹配的 `halfvec(2048)` 向量，并使用 HNSW 索引；
  关键词/相似度检索还可使用 `pg_trgm` 和 pgvector 扩展。
- 数据库由 AI Search 服务独立管理；FX Debate 运行时只通过 MCP 访问服务，不需要在
  Agent 项目中恢复快照、建表或重建向量。

自然语言查询不会直接决定物理表。服务端按以下顺序收敛查询：

```text
自然语言 query
  → instrument / dataset 检索
  → 候选一致性校验
  → 回查 source.dataset_catalog
  → 读取 source.dataset_field_catalog
  → 确认 instrument / provider
  → 选择白名单业务适配器
  → 安全查询适配器
  → status + data (+ 允许的 provenance metadata)
```

候选不在正式目录、供应商不一致、字段目录缺失或适配器不存在时，服务立即返回结构化
错误，不继续访问业务表。这样可以支持后续货币对别名、供应商代码和模糊查询扩展，
同时避免模型直接生成表名、字段名或 SQL。

### `unified_search` 优先策略

FX Debate 对数据服务的默认入口是 `unified_search`。它通过数据集目录自动识别是最新价格、
历史行情、宏观观测还是新闻，不把这些意图硬编码到 Agent prompt 中。当前 Debate 的四个
查询计划都会调用 `unified_search`：

| 查询 | 发送给 `unified_search` 的自然语言意图 |
| --- | --- |
| 最新报价 | 查询 EURUSD 的最新价格、买价、卖价和中间价 |
| 历史行情 | 查询 EURUSD 指定日期范围的日线 OHLCV |
| 宏观证据 | 查询该货币对相关的宏观指标观测值、实际值和预测值 |
| 新闻证据 | 查询指定日期范围内该货币对相关新闻、标题和摘要 |

`latest_prices_search`、`market_bars_search`、`macro_observations_search` 和
`news_articles_search` 仍然保留，主要用于数据端独立测试、顶层显式领域查询和兼容旧调用方。
除非用户明确指定领域或兼容接口，新增代码应优先使用 `unified_search`。

主 Agent 的 MCP stdio 面只注册 `unified_search`。四个独立工具不是已注册的 MCP 工具，
也不会被删除；它们继续作为 AI Search 的 HTTP 测试和兼容接口存在。

### HTTP 接口和 MCP 返回协议

主 Agent 的正式接入方式是本地 MCP stdio，MCP 服务只暴露：

```text
unified_search
```

MCP 输入和输出使用与统一 HTTP 接口相同的业务结构。主 Agent 启动查询时会自动启动
`external/market-data-tools` 的 MCP 子进程，不需要另外启动 AI Search HTTP 服务。

AI Search 仍保留以下 HTTP 接口，供前端、单路线测试和旧调用方使用：

独立服务提供以下正式业务接口：

```text
POST /tools/unified_search
POST /tools/latest_prices_search
POST /tools/market_bars_search
POST /tools/macro_observations_search
POST /tools/news_articles_search
POST /v1/evidence/{tool_name}
GET  /tools/definitions
GET  /health
```

请求只允许自然语言和受控过滤字段：

```json
{
  "query": "查询 EURUSD 最近一个月的日线行情",
  "provider": null,
  "start_date": "2026-07-01",
  "end_date": "2026-07-31",
  "max_rows": 250
}
```

普通工具接口保持兼容的精简协议：

```json
{
  "status": "success",
  "data": [
    {"bar_time": "2026-07-31", "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.105}
  ]
}
```

`/v1/evidence/{tool_name}` 仍然保留给旧 HTTP 调用方，但 FX Debate 默认不再访问该接口。

## 数据查询 Agent 与五 Agent Debate 的关系

`FxDataQueryAgent` 是数据查询专职组件，不是第六个 Debate 角色。它有两个调用入口：

1. **顶层直接查询**：注册为 `query_fx_data` Tool。用户可以直接问“查询 EURUSD 最近一个月
   的日线行情”或“找出 EURUSD 近期相关新闻”，Agent 将原问题转发给 AI Search，并返回
   结构化数据，不启动五 Agent Debate。
2. **FX Debate 数据准备**：`run_fx_debate` 创建 Evidence Context 后，生成价格、行情、
   宏观和新闻四个受控自然语言查询，优先全部使用 `unified_search`。返回结果经过字段
   适配、时间边界和数据质量检查，再冻结为 Evidence Bundle，五个 Debate Agent 只读取
   当前 Context 内的证据。

```text
直接数据问题 ───────────────→ query_fx_data → AI Search → 结构化 data

FX Debate 问题
  → Fx Router
  → run_fx_debate
  → FxDataQueryAgent / unified_search × 4
  → Evidence Context + Evidence Bundle
  → Pair Bull / Pair Bear / Macro + Technical
  → Debate Judge / FX PM
  → FX Risk Officer
  → 结构化最终报告
```

查询失败不会静默换成股票、外部实时行情或另一个数据源：部分领域失败会进入
`partial`/`insufficient_evidence`，全部失败返回 `FX_DATA_UNAVAILABLE`。当前 AI Search 的
`market_bars` 适配器主要提供日线数据，因此 4H 证据不足时必须在报告中显式标记，不能把
日线数据伪装成 4H。

## 目录与端口

| 组件 | 目录/入口 | 默认端口 | 用途 |
| --- | --- | ---: | --- |
| 主 API | `agent/api_server.py` | `8899` | Session、SSE、Swarm、设置和健康检查 |
| FX Debate 前端 | `frontend-fx-debate/` | `5898` | 日常对话和可视化工作区 |
| AI Search 服务 | `external/market-data-tools/` | `8011` | 自然语言数据检索和证据服务 |

FX 前端默认通过 Vite 代理访问 `http://127.0.0.1:8899`。如果后端地址不同，可设置 `VITE_API_URL`，或在前端“设置”页修改工作区后端地址。

## 环境准备

需要：

- Python 3.11 或更高版本；
- Node.js 18 或更高版本；
- 一个可用的 LLM 供应商和模型；
- 独立 AI Search 数据服务及其服务端配置。

本项目可以复用同级 `Vibe-Trading/.venv`。如果本机没有该虚拟环境，请按团队现有 Python 依赖创建环境后再启动；不要把虚拟环境、密钥或数据导出文件提交到 Git。

## 配置

先复制配置模板，在本机编辑：

```bash
cd /Users/xiaoranguo/Documents/ZJU/project/ICBC_intern/harness-finmarket-multi-agent-collab
cp agent/.env.example agent/.env
```

在 `agent/.env` 中选择一个 LLM 供应商。模板只包含占位符，真实 API key 只放在本机环境变量或本机 dotenv 中，例如：

```env
LANGCHAIN_PROVIDER=openrouter
LANGCHAIN_MODEL_NAME=your-model-name
OPENROUTER_API_KEY=your-local-key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

也可以不手工编辑模型配置，启动主 API 后打开前端“设置”页，选择供应商、模型和 API 基地址，点击“测试模型接口”确认 `/models` 可访问，再保存配置。自建 OpenAI-compatible 服务填写 API 基地址，例如 `https://your-host.example/v1`；不要填写网页首页或完整的 `/chat/completions` 地址。

前端普通连接参数保存在当前浏览器，输入的密钥只保存在当前标签页会话；点击保存后，后端会将配置写入用户级配置文件 `~/.vibe-trading/.env`。README、代码仓库和截图中都不应出现真实密钥。

### AI Search 数据源

```env
FX_DEBATE_DATA_SOURCE=ai_search
FX_DATA_SERVICE_ENABLED=1
FX_DATA_SERVICE_MAX_ROWS=250
FX_DATA_MCP_COMMAND=
FX_DATA_MCP_ARGS=
FX_DATA_MCP_SERVER_MODULE=backend.mcp_server
FX_DATA_MCP_WORKING_DIRECTORY=
FX_DATA_MCP_TIMEOUT_SECONDS=30
```

这里的配置告诉 Agent 如何通过本地 MCP stdio 启动独立 AI Search 服务。数据服务自己的
Embedding、候选模型和存储配置只由数据服务维护，不能混入 `agent/.env`。

在 `ai_search` 模式下，`FX_DATA_SERVICE_ENABLED=1` 还会向顶层 Agent 注册
`query_fx_data`。如果只想让 FX Debate 使用服务、不开放顶层数据查询，可以保留
`FX_DEBATE_DATA_SOURCE=ai_search`，但将 `FX_DATA_SERVICE_ENABLED=0`；Debate 内部仍会访问
MCP `unified_search`。修改 `agent/.env` 后必须重启主 API 服务。

独立 AI Search 服务的内部配置由数据端维护，Agent 只接收 MCP 结果，不需要知道其
Embedding、候选模型或存储凭据。部署说明见
[`external/market-data-tools/README.integration.md`](external/market-data-tools/README.integration.md)。

## 启动方式

### 1. 准备 AI Search MCP 服务

AI Search 是 FX Debate 唯一的数据入口。先按数据端说明准备服务端私有配置和依赖；
主 Agent 启动时会自动执行：

```bash
cd external/market-data-tools
python -m pip install -r requirements.txt
python -m backend.mcp_server
```

上面的命令用于单独检查 MCP stdio 协议；正常运行不需要手动保持该进程窗口。

### 2. 启动主 API

在仓库根目录执行：

```bash
../Vibe-Trading/.venv/bin/python -m uvicorn \
  --app-dir agent \
  api_server:app \
  --host 127.0.0.1 \
  --port 8899
```

看到 API 监听 `127.0.0.1:8899` 后保持该终端运行。

### 3. 启动 FX 前端

另开终端：

```bash
cd frontend-fx-debate
npm install
npm run dev
```

浏览器打开：<http://127.0.0.1:5898/>

推荐流程：先进入“设置”页测试后端连接和模型接口，再进入“对话”页发送：

```text
分析 EURUSD 未来两周走势，结合 4H 和 1D 周期，给出平衡风险偏好的交易建议，并明确入场、止损、止盈和失效条件。
```

运行过程中可以切换到“协作画布”查看实际任务依赖，切换到“流程日志”查看 Agent、Tool、
AI Search 和证据事件，运行完成后在“最终报告”查看结构化结论。Session、run 和 view 会写入
URL，刷新页面可以恢复对应运行。

## 前端视图

FX 前端支持以下 URL：

```text
/?view=chat      对话
/?view=canvas    协作画布
/?view=data      数据概览
/?view=logs      流程日志
/?view=report    最终报告
/?view=settings  API 配置与测试
```

当一个 Session 中多次发送 FX Debate 问题时，每次发送都会生成独立的 run；画布、数据和报告通过当前 run 选择器切换，不会把不同运行的证据混在一起。

## 测试与构建

前端单元测试和生产构建：

```bash
cd frontend-fx-debate
npm run test:run
npm run build
```

FX 路由、证据、Tool 注册和运行时测试（复用上游虚拟环境）：

```bash
../Vibe-Trading/.venv/bin/python -m pytest -q \
  agent/tests/test_fx_router.py \
  agent/tests/test_fx_debate_request_adapter.py \
  agent/tests/test_run_fx_debate_tool.py \
  agent/tests/test_fx_debate_tool_registration.py \
  agent/tests/test_fx_debate_preset.py \
  agent/tests/test_fx_evidence_factory_v2.py \
  agent/tests/test_fx_market_evidence_tools.py \
  agent/tests/test_fx_bundle_tools.py \
  agent/tests/test_validate_fx_output_v2.py
```

AI Search 服务测试：

```bash
curl -s http://127.0.0.1:8011/health
curl -s -X POST http://127.0.0.1:8011/tools/unified_search \
  -H 'Content-Type: application/json' \
  -d '{"query":"查询 EURUSD 最近一个月的日线行情","max_rows":30}'
```

## 常见问题

**对话返回 HTTP 500**

先确认主 API 是 `8899`，再在“设置”页测试后端连接。若数据未就绪，检查 AI Search
MCP 依赖、数据库隧道、模型配置和 MCP 启动日志；修改 dotenv 后重启 API。

**模型供应商测试失败**

确认接口地址是 OpenAI-compatible 的基地址，通常以 `/v1` 结尾；确认模型供应商允许 `GET /models`，并检查 API key 是否有访问模型列表的权限。自建服务不需要鉴权时可以留空 key。

**FX 数据不可用**

确认 `FX_DEBATE_DATA_SOURCE=ai_search`、MCP 工作目录和 AI Search 的数据库配置。服务不可用时
系统会明确返回数据不可用，不会将 FX 请求静默降级为股票分析或其他数据源。

**画布显示等待依赖或阻塞**

这是服务端任务依赖尚未完成或前置任务失败的状态，不代表前端自己推断失败。优先查看“流程日志”中的原始事件和当前 run；如果主 API 已重启，刷新页面会通过 `/swarm/runs/{id}` 补全历史状态。

**停止运行后无法再次提问**

确认当前 run 已进入“已取消/失败/已完成”，然后重新发送问题；每次发送会创建新的 run。若页面仍显示运行中，刷新 Session 或重新打开 `/?view=chat&session=<session_id>`。

## 安全边界

- 不提交 `agent/.env`、`external/market-data-tools/.env`、AI Search 服务凭据、LLM/API key、Token 和 `agent/.swarm/runs/` 运行产物。
- 日志和前端事件会对常见凭据字段做脱敏，但不要把真实凭据粘贴到 prompt、截图、Issue 或 README。
- FX Debate 是研究和决策支持链路，不包含交易执行、自动下单或资金操作。

## 进一步文档

- [`frontend-fx-debate/README.md`](frontend-fx-debate/README.md)：独立前端的简要说明。
- [`docs/team-progress/fx_debate_agents_tools_implementation.md`](docs/team-progress/fx_debate_agents_tools_implementation.md)：Evidence Context、Tool 和五 Agent 实现细节。
- [`docs/team-progress/fx_router_robustness_test_report_20260818.md`](docs/team-progress/fx_router_robustness_test_report_20260818.md)：路由验收矩阵和 Session/SSE 冒烟结果。
- [`external/market-data-tools/README.integration.md`](external/market-data-tools/README.integration.md)：AI Search 服务接入、HTTP 接口和独立部署说明。
