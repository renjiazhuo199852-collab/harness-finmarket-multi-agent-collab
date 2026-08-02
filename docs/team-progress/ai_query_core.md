# AI 智能查询核心：PR 1

## 1. 目标与边界

本次 PR 先实现 leader 要求的第一条验收路径：

```text
用户问题：查询 EUR/USD 最新价格
  -> 精确代码、关键词和 Embedding 混合检索目录
  -> RRF 合并候选
  -> 补齐同一数据集的字段和安全关系上下文
  -> 接收结构化查询计划
  -> 程序校验数据集、字段、关系和返回行数
  -> 生成参数化只读 SQL
  -> 返回 EUR/USD 最新报价
```

本 PR **还没有**新增 Agent Tool，也没有修改 AgentLoop 的 Tool 注册表。它提供
可复用的 Python 核心模块和本机验证脚本；`search_data_catalog`、
`execute_query_plan` 以及 Agent Function Calling 接入放在 PR 2。

本阶段不执行以下行为：

- 不接受或执行模型传入的原始 SQL；
- 不允许模型任意指定表名、列名或 JOIN 条件；
- 不写入远端 Phase 2 PostgreSQL；
- 不修改 `icbc_trading` 或原始 Excel；
- 不在 CI 中调用真实 Embedding、数据库或 LLM API。

## 2. 本次代码改动

| 文件 | 职责 |
| --- | --- |
| `agent/src/config/env_schema.py` | 增加独立的 `AI_QUERY_*` 数据库、Embedding、行数和时效配置。默认关闭。 |
| `agent/src/ai_query/catalog_search.py` | 精确工具别名、PostgreSQL 关键词检索、智谱 `embedding-3`、等权 RRF 和目录上下文补全。 |
| `agent/src/ai_query/query_executor.py` | 校验结构化查询计划，从 `ai.dataset_policy`、`ai.field_mapping` 和受控关系生成参数化 SQL。 |
| `agent/src/ai_query/__init__.py` | 导出检索器、执行器及核心异常。 |
| `agent/scripts/verify_ai_latest_price.py` | 读取本机配置，真实验证 EUR/USD 目录检索和最新报价查询。 |
| `agent/tests/test_ai_catalog_search.py` | 验证别名、RRF、字段/关系上下文和 Embedding 降级。 |
| `agent/tests/test_ai_query_executor.py` | 验证计划拒绝、字段映射、参数绑定、EUR/USD 别名和 SQL 运算符。 |
| `agent/tests/test_env_schema.py` | 验证 AI 查询配置默认关闭及环境变量读取。 |

## 3. 数据库前提

核心模块连接本机独立数据库：

```text
host=127.0.0.1
port=5432
database=icbc_finmarket_ai
user=<本机 PostgreSQL 用户>
```

数据库包含两个职责不同的 Schema：

```text
source
  Excel 快照的源表镜像，例如 instrument_master、instrument_identifier、latest_prices。

ai
  dataset_policy、field_mapping、semantic_relations、search_documents 等 AI 元数据。
```

`ai.search_documents.embedding` 保存 2048 维 `embedding-3` 向量，关键词检索使用
`search_vector`。真实验证使用的目录快照版本为 `db_export_0802`。

本 PR 假定 `icbc_finmarket_ai` 已由项目负责人按当前数据库方案建立并完成向量化；
代码只读，不负责创建库、导入 Excel 或调用 Embedding 生成文档向量。

## 4. 本机配置

复制示例文件后，在 `agent/.env` 填入本机值：

```powershell
Copy-Item agent\.env.example agent\.env
```

至少配置：

```text
AI_QUERY_ENABLED=1
AI_QUERY_DB_HOST=127.0.0.1
AI_QUERY_DB_PORT=5432
AI_QUERY_DB_NAME=icbc_finmarket_ai
AI_QUERY_DB_USER=<本机 PostgreSQL 用户>
AI_QUERY_DB_PASSWORD=<本机 PostgreSQL 密码>

AI_QUERY_EMBEDDING_ENABLED=1
ZHIPU_API_KEY=<个人智谱 API Key>
ZHIPU_EMBEDDING_ENDPOINT=https://open.bigmodel.cn/api/paas/v4/embeddings
ZHIPU_EMBEDDING_MODEL=embedding-3
```

没有配置 `ZHIPU_API_KEY` 或向量服务失败时，检索会降级到精确匹配和关键词检索；
执行器仍可使用。数据库密码和 API Key 只能保存在 Git 忽略的本机配置或进程环境中，
不得写入代码、日志、截图、Issue、PR 或提交。

## 5. 目录检索接口

Python 调用形态：

```python
from src.ai_query import AICatalogSearch

result = AICatalogSearch().search("查询 EUR/USD 最新价格", limit=10)
```

检索阶段不返回行情业务行，而返回目录候选，例如：

```text
instrument:FX_EURUSD
  -> instrument_master

dataset:LSEG_SPOT_PRICE
  -> latest_prices

relation:instrument_to_identifier
relation:identifier_to_latest_prices

field:LSEG_SPOT_PRICE.PRICE_TIME
field:LSEG_SPOT_PRICE.LAST
field:LSEG_SPOT_PRICE.BID
field:LSEG_SPOT_PRICE.ASK
field:LSEG_SPOT_PRICE.MID
```

`EURUSD`、`EUR/USD`、`FX_EURUSD` 和 `EUR=` 会进入同一套精确工具解析逻辑。
Embedding 只负责查询向量，不把用户问题直接变成 SQL；RRF 之后的上下文补全从
受控 `ai` 元数据中补齐同一数据集的字段和关系。

## 6. 结构化查询计划

执行器接受的计划只允许以下顶层字段：

```json
{
  "dataset_id": "LSEG_SPOT_PRICE",
  "entity": {
    "type": "instrument",
    "value": "EURUSD"
  },
  "select": ["PRICE_TIME", "LAST", "BID", "ASK", "MID"],
  "filters": [
    {"field": "SOURCE", "operator": "eq", "value": "LSEG"}
  ],
  "order_by": [
    {"field": "PRICE_TIME", "direction": "desc"}
  ],
  "limit": 1
}
```

程序随后执行以下校验：

1. `dataset_id` 必须登记在 `ai.dataset_policy` 且允许查询；
2. 选择、过滤和排序字段必须登记在 `ai.field_mapping`；
3. 字段必须允许相应操作，并映射到受控 `source` 表列；
4. 工具必须能唯一解析到 `instrument_master`；
5. 操作符、排序方向和 `limit` 必须在白名单范围内；
6. 所有用户值使用 PostgreSQL 参数绑定，表名和列名只来自已校验元数据。

当前 PR 只开放：

```text
LSEG_SPOT_PRICE -> source.latest_prices
```

执行结果包含：

```text
dataset_id: LSEG_SPOT_PRICE
storage_table_name: latest_prices
source_version: db_export_0802
instrument.instrument_id: FX_EURUSD
data[0].price_time: 2026-08-01T03:53:45.756357+08:00
data[0].last: 1.1528
data[0].bid: 1.1527
data[0].ask: 1.1529
data[0].mid: 1.1528
```

报价时间超过配置阈值时，结果会带 `warnings`，不会把旧快照伪装成实时价格。

## 7. 复刻测试

### 7.1 不访问网络的核心测试

在仓库根目录运行：

```powershell
python -m pytest `
  agent/tests/test_env_schema.py `
  agent/tests/test_ai_catalog_search.py `
  agent/tests/test_ai_query_executor.py -q
```

当前结果为 `98 passed`。测试使用假的数据库客户端和假的 Embedding 客户端，
不会需要 SSH、数据库密码、智谱 API Key 或真实网络。

### 7.2 真实本机验证

确认本机 PostgreSQL 已启动、`icbc_finmarket_ai` 已存在，并在 `agent/.env` 配置
数据库和智谱凭据后运行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "agent")
python agent/scripts/verify_ai_latest_price.py
```

脚本会打印两部分结果：

1. 目录候选：工具、数据集、两条正式关系和报价字段；
2. 真实报价：`last=1.1528`、`bid=1.1527`、`ask=1.1529`、`mid=1.1528`，以及
   `source_version=db_export_0802`。

当前样例报价不是实时生产行情，脚本可能同时打印时效警告，这是预期行为。

## 8. 后续 PR 2

PR 2 将在本 PR 的分支基础上新增：

```text
search_data_catalog
execute_query_plan
```

两个 Agent Tool 默认受 `AI_QUERY_ENABLED` 控制；开关关闭时不注册，旧的四个
Phase 2 市场数据 Tool 保持原行为。PR 2 还需要补齐 Tool Schema、Agent 注册测试
和真实 AgentLoop 手工验证，完成后单独创建 PR，不直接合并 `main`。
