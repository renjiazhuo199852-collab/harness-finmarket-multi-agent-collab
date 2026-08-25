# ICBC Trading Portable AI Search

这是一个可以整体复制到其他 Agent 项目的独立 AI Search 工具目录。它自己包含：

- 查询解析、数据集目录发现、金融工具确认、字段目录解析和四张业务表适配器；
- Embedding 语义检索和聊天模型候选筛选；
- 一个统一查询工具和四个独立业务工具；
- 一个仅供 HTTP/Python 测试的 `instrument_search` 标准金融工具路由；
- Python 函数接口、MCP stdio 接口、HTTP 接口和 Agent 函数工具定义；
- 数据库完整快照、前端测试工作台和接口协议测试。

主 Agent 的正式接入方式是本地 MCP stdio，MCP 只暴露 `unified_search`。四个独立业务工具
仍保留为 HTTP 测试和兼容接口，不是主 Agent 的 MCP 工具。

运行时不依赖上级目录的 `ICBC-trading-ai_search`。原项目仍作为开发和历史验证项目保留；
本目录是可以交给其他 Agent 项目使用的完整版本。

## 交付须知：服务器数据库无需重建

服务器端的 `icbc_shared` 数据库已经由管理员完成配置并通过验收，以下内容均已存在，
不需要在其他 Agent 项目中重复执行：

- `source` Schema 的 9 张原有正式业务表，以及新增的
  `source.instrument_metric_link` 宏观关系表；
- `ai_search` Schema 和 3 张检索表；
- 全部检索文档、`halfvec(2048)` Embedding 和 HNSW 索引；
- `source.dataset_catalog` 已登记 `INSTRUMENT_MASTER`，数据集检索文档共 8 条；
- `pg_trgm`、pgvector 扩展、索引和约束。

复制本 `tools` 文件夹后，日常使用只需要建立 SSH 隧道、安装依赖并启动后端。**不要**执行
数据库恢复、Schema 建表、检索文档重建或 Embedding 重建脚本。`database/full_database.sql`
仅作为备份和灾备迁移快照，不是日常启动依赖。

## 当前部署状态

服务器上的 `icbc_shared` 数据库已经完成初始化和验收，包含 `source` 九张原有正式表和
`instrument_metric_link` 关系表、
`ai_search` 三张检索表、Embedding 数据和 HNSW 索引。日常使用不需要重新建表、恢复
数据库、重建检索文档或重新生成 Embedding。

本机运行 tools 时，先由用户手动建立 SSH 隧道，再启动后端。数据库密码和模型密钥
只从本机 `.env` 读取，SSH 密码不写入项目文件。

## 一、五个工具

| 工具 | 用途 | 主要参数 |
| --- | --- | --- |
| `unified_search` | 根据数据集目录自动发现查询类型 | `query`、`provider`、日期、`max_rows` |
| `latest_prices_search` | 查询最新价格 | `query`、`provider` |
| `market_bars_search` | 查询日线或小时原始历史行情（1H，可供上层聚合 4H） | `query`、`provider`、日期、`max_rows` |
| `macro_observations_search` | 查询宏观指标观测值 | `query`、`provider`、日期、`max_rows` |
| `news_articles_search` | 查询相关新闻 | `query`、`provider`、日期 |

新闻工具不限制最终新闻候选条数。`max_rows` 不是新闻工具的输入参数。
Agent 不需要也不能传入 `route`、物理表名、字段名、SQL、Embedding 开关或候选模型开关。

标准金融工具路由：

```text
POST /tools/instrument_search
```

它只用于把用户输入解析为 `instrument_master` 中已经存在且 active 的标准
`canonical_symbol`。该路由不是第二个 MCP 工具，主 Agent 仍然只调用 `unified_search`。

## MCP stdio 接入

主 Agent 通过本地 MCP stdio 启动本项目的 MCP 子进程，正式工具面只有：

```text
unified_search
```

手动检查 MCP 服务：

```powershell
cd D:\python\projects\ICBC-trading\harness-finmarket-multi-agent-collab\external\market-data-tools
python -m backend.mcp_server
```

正常使用时不需要单独保持这个进程；主 Agent 会按需启动并关闭它。MCP 服务加载本项目
`.env` 中的数据库、Embedding 和聊天模型配置，调用参数中不包含任何密钥。

## 二、统一查询流程

```text
用户问题
  -> 查询解析
  -> dataset_catalog 混合检索
  -> 候选大模型只能从检索候选中选择
  -> 数据集候选一致性校验
  -> source.dataset_catalog 正式回查
  -> source.dataset_field_catalog 读取字段
  -> 根据 storage_table_name 选择安全适配器
  -> 按需执行 instrument_master
  -> 按需执行 instrument_identifier
  -> 查询业务表
  -> 返回 status + data
```

数据集目录本身就是意图候选来源。统一接口不会把“最新价格、历史行情、宏观指标、新闻”
硬编码成自然语言意图枚举，而是检索 `ai_search.dataset_search_documents`，再由模型从
检索返回的候选中选择。模型返回候选集合之外的 `dataset_id`、候选不一致或正式目录回查
失败时，查询立即结束，不访问字段目录和业务表。

适配器注册表只负责安全地处理已经从正式目录返回的物理表名：

```text
latest_prices       -> latest_prices 适配器
market_bars         -> market_bars 适配器
macro_observations  -> macro_observations 适配器
news_articles       -> news_articles 适配器
instrument_master   -> instrument_master 标准化适配器
```

只查询标准金融工具时，统一入口会选择 `INSTRUMENT_MASTER` 数据集：

```text
查询欧元兑美元的标准代码
  -> dataset_catalog 选择 INSTRUMENT_MASTER
  -> dataset_field_catalog 确认标准化返回字段
  -> instrument_search_documents 四路检索
  -> source.instrument_master 正式回查
  -> 返回 canonical_symbol
```

该场景跳过 `instrument_identifier`，因为当前只需要标准代码，不需要供应商代码。

结构化业务路线会确认金融工具和供应商标识：

```text
instrument_master
  -> instrument_identifier
  -> dataset_catalog
  -> dataset_field_catalog
  -> 业务表
```

新闻路线不经过 `instrument_master` 和 `instrument_identifier`，但仍然经过数据集目录和
字段目录：

```text
dataset_catalog
  -> dataset_field_catalog
  -> 新闻四路检索
  -> source.news_articles
```

相关宏观查询使用正式关系表，不能把外汇工具 ID 直接当作宏观指标 ID：

```text
EURUSD
  -> source.instrument_master 确认 FX_EURUSD 为 active
  -> source.instrument_metric_link 取得欧元区和美国 METRIC 关系
  -> 使用 metric_id + source 查询 source.macro_observations
```

服务器已登记 16 条 EURUSD 关系：欧元区 5 条 `base_currency`、美国 11 条
`quote_currency`。首期只支持 `METRIC`，不会把 `INTEREST_RATE` 或 `BOND_YIELD` 混入结果。

## 三、返回协议

正式 Python 和 HTTP 工具都只返回业务结果：

```json
{
  "status": "success",
  "data": [
    {
      "price_time": "2026-08-01T03:53:45+08:00",
      "last": "1.1528000000",
      "bid": "1.1527000000",
      "ask": "1.1529000000",
      "mid": "1.1528000000"
    }
  ]
}
```

无数据仍使用 `status=success` 和空数组。目录不一致、字段失败、模型不可用或配置异常
使用结构化错误：

```json
{
  "status": "rejected",
  "data": [],
  "error": {
    "code": "DATASET_NOT_FOUND",
    "message": "未找到匹配的数据集"
  }
}
```

常见错误码包括：`DATASET_NOT_FOUND`、`DATASET_INTENT_MISMATCH`、
`DATASET_CANDIDATE_INVALID`、`DATASET_PROVIDER_MISMATCH`、`ADAPTER_NOT_REGISTERED`、
`MACRO_RELATION_NOT_FOUND`、`MACRO_RELATION_PROVIDER_MISMATCH`、
`MACRO_METRIC_NOT_FOUND`、`MACRO_RELATION_INACTIVE`、`MACRO_FIELD_RESOLUTION_FAILED`、
`TOOL_NOT_FOUND` 和 `SERVICE_ERROR`。

## 四、配置和密钥

`tools/.env` 已配置服务器数据库连接、SiliconFlow Embedding 配置和聊天模型配置。数据库连接
通过 SSH 隧道访问服务器：本机端口是 `15433`，服务器 PostgreSQL 实际端口是 `5433`。
复制整个目录后不需要重复恢复数据库，只需先建立隧道并启动后端。

`tools/.env.example` 只包含占位符，便于重新部署。当前 Embedding 配置为
SiliconFlow 的 `Qwen/Qwen3-Embedding-8B`，请求维度为 `2048`。实际配置包括：

```text
AI_SEARCH_DB_HOST
AI_SEARCH_DB_PORT
AI_SEARCH_DB_NAME
AI_SEARCH_DB_USER
AI_SEARCH_DB_PASSWORD
EMBEDDING_BASE_URL
EMBEDDING_ENDPOINT（兼容旧配置；设置 Base URL 时无需填写）
EMBEDDING_MODEL
EMBEDDING_DIMENSIONS
EMBEDDING_API_KEY
LLM_BASE_URL
LLM_CHAT_COMPLETIONS_PATH
LLM_API_KEY
LLM_MODEL
LLM_REASONING_EFFORT
```

真实密钥只保存在 `.env`，不写入源码、SQL 或文档。`.env` 已加入 `.gitignore`，这个目录
只能作为私有项目管理。部署到正式环境时，建议用系统环境变量覆盖 `.env`，不要把密钥
提交到版本库。

本机连接服务器数据库前，在另一个 PowerShell 或终端窗口执行，并保持窗口开启：

```powershell
ssh -p 22 -L 15433:127.0.0.1:5433 root@101.35.55.7
```

不要把 SSH 密码写入 `.env`。如果以后后端部署到服务器本机，数据库连接端口应改回
服务器本机的 `5433`，不再需要这条 SSH 隧道。

更换为其他数据库时才修改：

```text
AI_SEARCH_DB_HOST
AI_SEARCH_DB_PORT
AI_SEARCH_DB_NAME
AI_SEARCH_DB_USER
AI_SEARCH_DB_PASSWORD
```

查询代码、数据集目录逻辑和业务适配器不需要修改。

## 五、数据库恢复

`database/full_database.sql` 是从服务器当前 `icbc_shared` 导出的备份快照，包含：

- `source` Schema 的 9 张原有正式业务表和 `instrument_metric_link` 关系表；
- `ai_search` Schema 和三张 AI 检索文档表；
- 全部数据、`halfvec(2048)` Embedding、HNSW 索引、约束和序列；
- `pg_trgm`、`vector` 扩展创建语句。

服务器数据库已经配置完成，正常启动时**不要**执行数据库恢复、创建表、执行关系迁移、重建检索文档
或重新生成 Embedding。快照仅用于灾备、迁移到另一台数据库或管理员明确批准的恢复操作。

本次 Embedding 模型切换或模型重新部署时，才执行全量向量重建。执行前应停止后端，
保持 SSH 隧道开启，并确认已经完成数据库备份：

```powershell
python .\scripts\rebuild_embeddings.py
```

该脚本会先生成并校验三张检索表的全部向量，全部成功后才更新数据库并重建三套 HNSW
索引。正常启动流程不调用该脚本。

恢复脚本默认会拒绝对正式数据库 `icbc_shared` 执行操作：

```powershell
cd D:\python\projects\ICBC-trading\tools
.\scripts\restore_database.ps1
```

如确需进行灾备恢复，必须由管理员确认目标数据库和快照来源后，显式传入：

```powershell
.\scripts\restore_database.ps1 -AllowExistingServerDatabase
```

数据库备份和恢复边界见 [`database/README.md`](database/README.md)。关系迁移
`sql/004_create_instrument_metric_link.sql` 已在服务器执行完成，日常启动不需要重复执行。
仅在恢复到另一台空数据库后，按 003、004 的顺序执行迁移。恢复后才需要运行：

```powershell
python .\scripts\check_config.py
```

## 六、启动后端

先确认 SSH 隧道已经在另一个终端运行：

```powershell
ssh -p 22 -L 15433:127.0.0.1:5433 root@101.35.55.7
```

在 `tools` 根目录安装依赖：

```powershell
cd D:\python\projects\ICBC-trading\tools
python -m pip install -r requirements.txt
```

启动 FastAPI：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
```

健康检查：

```text
http://127.0.0.1:8011/health
```

健康检查只返回数据库和模型是否配置，不返回任何密码或 API Key。数据库检查成功时，
表示本机后端已经通过 SSH 隧道连接到服务器的 `icbc_shared`。

## 七、MCP stdio 调用

主 Agent 的正式接入方式是 MCP stdio，服务只暴露 `unified_search`。主 Agent 启动查询时
会自动执行本项目的 MCP 子进程，不需要单独启动 HTTP 服务：

```powershell
cd D:\python\projects\ICBC-trading\harness-finmarket-multi-agent-collab\external\market-data-tools
python -m backend.mcp_server
```

上面的命令用于人工检查 MCP 协议，启动后保持当前终端即可观察 stderr 日志；正常由主
Agent 启动时不需要手动执行。

## 八、HTTP 调用（测试和兼容）

以下接口用于前端、独立业务路线测试和旧调用方兼容：

```text
POST /tools/unified_search
POST /tools/latest_prices_search
POST /tools/market_bars_search
POST /tools/macro_observations_search
POST /tools/news_articles_search
POST /tools/instrument_search
```

统一查询示例：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8011/tools/unified_search `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"查询 EURUSD 的最新价格"}'
```

历史行情示例：

```json
{
  "query": "查询 EURUSD 最近一个月的日线行情",
  "start_date": "2026-07-01",
  "end_date": "2026-08-01",
  "max_rows": 100
}
```

新闻示例：

```json
{
  "query": "查询 EURUSD 最近一个月的相关新闻",
  "start_date": "2026-07-01",
  "end_date": "2026-08-01"
}
```

调试前端使用的 SSE 接口在正式查询路径后追加 `/stream`，例如：

```text
POST /tools/unified_search/stream
POST /tools/news_articles_search/stream
```

SSE 会返回查询解析、数据集四路检索、RRF、候选模型、目录回查、字段解析、主数据校验
和业务适配器等阶段；这些内部信息不会出现在正式工具响应中。

## 八、Python 和 Agent 调用

在 `tools` 根目录执行：

```python
from backend.ai_search import unified_search

result = unified_search("查询 EURUSD 的最新价格")
print(result)
```

向 Agent 注册工具：

```python
from backend.ai_search import get_tool_definitions, invoke_tool

definitions = get_tool_definitions()
result = invoke_tool(
    "news_articles_search",
    {
        "query": "查询 EURUSD 最近一个月的相关新闻",
        "start_date": "2026-07-01",
        "end_date": "2026-08-01",
    },
)
```

`get_tool_definitions()` 返回 OpenAI 兼容的函数工具定义；`invoke_tool()` 是不依赖具体
Agent 框架的统一调用入口。

## 九、前端测试工作台

启动前端：

```powershell
cd D:\python\projects\ICBC-trading\tools\front
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173
```

Vite 默认把 `/tools` 代理到 `http://127.0.0.1:8011`。需要连接云端后端时，可以在
启动前设置：

```powershell
$env:AI_SEARCH_TOOLS_API_TARGET = "http://云服务器地址:8011"
npm run dev
```

前端保留首页、统一查询页面和四个独立业务测试页面。每个页面都通过 SSE 展示模块的
输入、输出、状态、耗时和错误；页面不会读取数据库密码或模型 API Key。

## 十、测试和验收

运行工具协议测试：

```powershell
cd D:\python\projects\ICBC-trading\tools
python -m pytest tests -q
```

运行配置检查：

```powershell
python .\scripts\check_config.py
```

完整验收顺序：

```text
复制 tools 文件夹
  -> 安装 Python 和前端依赖
  -> 保持 SSH 隧道运行
  -> 检查 tools/.env 和服务器数据库连接
  -> 启动后端
  -> 启动前端
  -> 调用五个默认工具；标准化路由通过统一接口或 HTTP 兼容入口验证
  -> 验证四张业务表均返回真实结果
```

服务器端数据已经准备完成，验收流程不包含数据库恢复或 Embedding 重建。当前目录新增
`INSTRUMENT_MASTER` 后，服务器已有 8 条数据集目录记录、29 条字段目录记录；日常启动不
需要再次执行目录迁移或向量重建。

当前目录的 `legacy_tests` 保存原开发项目的历史测试样本；可移植项目默认运行
`tests` 下针对工具注册、HTTP 边界和公开响应协议的测试。
