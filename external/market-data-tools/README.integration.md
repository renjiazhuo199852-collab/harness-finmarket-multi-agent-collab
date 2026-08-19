# FX Debate 接入说明

本目录是数据端同事交付的独立 AI Search 服务副本。当前 Agent 项目不导入其中的
Python 模块，运行时通过本地 MCP stdio 访问它；这样数据端可以独立升级查询解析、
目录检索和数据库适配，而 FX Debate 只依赖稳定的接口协议。

主 Agent 的 MCP 面只暴露一个工具：

```text
unified_search
```

四个独立业务工具仍保留为 HTTP 测试和兼容接口，不是主 Agent 的 MCP 工具。

## 服务接口

原有接口保持不变：

```text
POST /tools/unified_search
POST /tools/latest_prices_search
POST /tools/market_bars_search
POST /tools/macro_observations_search
POST /tools/news_articles_search
```

FX Debate 使用新增的只读证据接口：

```text
POST /v1/evidence/{tool_name}
```

该接口在原有 `status + data` 之外保留经过白名单过滤的 `metadata`，用于保留行情时间、
新闻发布时间、宏观指标和供应商来源。它不暴露 SQL、候选文档、模型原始输出或数据库连接信息。

## 启动

不要把真实 `.env` 提交到仓库。先在本机准备独立服务的私有环境配置，再启动：

```bash
cd external/market-data-tools
python -m pip install -r requirements.txt
python -m backend.mcp_server
```

FX Debate 侧配置：

```text
FX_DEBATE_DATA_SOURCE=ai_search
FX_DATA_SERVICE_MAX_ROWS=250
FX_DATA_SERVICE_ENABLED=1
FX_DATA_MCP_COMMAND=
FX_DATA_MCP_ARGS=
FX_DATA_MCP_SERVER_MODULE=backend.mcp_server
FX_DATA_MCP_WORKING_DIRECTORY=
FX_DATA_MCP_TIMEOUT_SECONDS=30
```

`FX_DATA_SERVICE_ENABLED=1` 会向顶层 Agent 注册 `query_fx_data`，允许用户直接问“查询
EURUSD 最近一个月的日线行情”等数据问题。Debate 运行时则由 `FxDataQueryAgent` 生成
四类受控自然语言查询，并优先全部发送到 `unified_search`；领域语义（价格、日线、宏观、
新闻）通过查询文本表达，由数据端统一完成意图识别、数据集路由和结果结构化。返回结果会
冻结成现有 Evidence Bundle 后再交给五个 Debate Agent。顶层查询仍支持显式领域工具名，
用于兼容已有调用方，但底层统一通过 MCP 的 `unified_search` 完成。

正常启动主 Agent 时不需要单独启动 HTTP 服务；AI Search MCP 子进程由主 Agent 按需启动。
HTTP 服务仍可按本 README 的接口说明单独启动，用于前端和兼容测试。

当前服务的 `market_bars` 适配器主要提供日线原始数据，因此缺少 4H 时系统会标记为证据
不足，不会用其他行情源静默补齐。
