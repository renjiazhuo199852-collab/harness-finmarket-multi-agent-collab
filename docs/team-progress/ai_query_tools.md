# AI 智能查询 Agent Tool：PR 2

## 1. 新增内容

PR 2 把 PR 1 的 AI 查询核心接入项目现有 `ToolRegistry`，新增两个可被 Agent
识别的 Tool：

| Tool | 作用 | 是否直接返回业务数据 |
| --- | --- | --- |
| `search_data_catalog` | 根据自然语言问题检索工具、数据集、字段和安全关系候选 | 否，只返回目录元数据 |
| `execute_query_plan` | 校验结构化计划并执行受控参数化查询 | 是，返回允许的数据行 |

典型调用顺序：

```text
用户：查询 EUR/USD 最新价格
  -> search_data_catalog
       找到 FX_EURUSD、EUR=、LSEG_SPOT_PRICE、latest_prices、字段和关系
  -> Agent 根据候选组织结构化查询计划
  -> execute_query_plan
       程序校验计划并查询 source.latest_prices
  -> Agent 根据 JSON 结果回答用户
```

Agent 不能把原始 SQL、任意表名、任意列名或任意 JOIN 条件传给
`execute_query_plan`。计划必须经过 `ai.dataset_policy`、`ai.field_mapping` 和
内置安全关系校验。

## 2. Tool 输入

### `search_data_catalog`

```json
{
  "question": "查询 EUR/USD 最新价格",
  "limit": 10
}
```

`question` 必填，`limit` 可选，范围为 1 到 50。返回内容是候选数组，包含
`doc_id`、`doc_type`、`title`、`dataset_id`、`source_table`、`source_version`、
RRF 分数和关键词/向量排名。第一条验收路径应看到：

```text
instrument:FX_EURUSD
dataset:LSEG_SPOT_PRICE
relation:instrument_to_identifier
relation:identifier_to_latest_prices
field:LSEG_SPOT_PRICE.PRICE_TIME
field:LSEG_SPOT_PRICE.LAST
field:LSEG_SPOT_PRICE.BID
field:LSEG_SPOT_PRICE.ASK
field:LSEG_SPOT_PRICE.MID
```

### `execute_query_plan`

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

`dataset_id`、`entity` 和 `select` 必填；`filters`、`order_by` 和 `limit` 有默认值。
当前只开放 `LSEG_SPOT_PRICE -> source.latest_prices`。

## 3. 注册开关

两个 Tool 默认不注册：

```text
AI_QUERY_ENABLED=0
```

只有 `AI_QUERY_ENABLED=1` 且数据库主机、数据库名、用户和密码都配置完整时，
自动发现机制才会注册它们。向量服务不是注册前提；没有智谱 Key 时，
`search_data_catalog` 会降级到精确匹配和关键词检索。

这两个 Tool 与原有四个 Phase 2 Tool 使用不同配置：

```text
旧四个 Tool：MARKET_DB_* -> 远端 Phase 2 数据库
新两个 Tool：AI_QUERY_*  -> 本机 icbc_finmarket_ai
```

## 4. 复刻验证

### 4.1 不调用 LLM 的真实 Tool 验证

在仓库根目录确保 `agent/.env` 已配置本机 AI 数据库后运行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "agent")
python agent/scripts/verify_ai_query_tools.py
```

脚本会验证：

1. 两个 Tool 是否出现在 `ToolRegistry`；
2. OpenAI Function Schema 是否注册；
3. 目录检索是否返回 EUR/USD 相关候选；
4. 结构化计划是否返回 `last=1.1528`、`bid=1.1527`、`ask=1.1529`、`mid=1.1528`；
5. 返回的 `source_version` 是否为 `db_export_0802`。

### 4.2 单元测试

```powershell
python -m pytest `
  agent/tests/test_ai_query_tools.py `
  agent/tests/test_ai_catalog_search.py `
  agent/tests/test_ai_query_executor.py -q
```

测试不连接真实数据库、不调用智谱或 LLM API，当前结果为 `104 passed`。

### 4.3 真实 AgentLoop 手工验证

在完整项目依赖、本机 AI 数据库、个人 LLM Key 和 `agent/.env` 均就绪后启动：

```powershell
vibe-trading serve --port 8899
```

在本机 Agent 对话中输入：

```text
请查询 EUR/USD 最新价格。
必须先使用 search_data_catalog 找到相关工具、数据集、字段和关系，
再使用 execute_query_plan 执行结构化计划；禁止编写或执行原始 SQL。
```

观察 Agent 是否依次调用两个新 Tool，并根据真实返回生成最终回答。这个手工验证
需要真实 LLM，因此不会进入 GitHub CI；CI 只运行假的检索器、执行器和注册测试。

## 5. 安全限制

- Tool 只读，不提供 INSERT、UPDATE、DELETE 或 DDL 能力；
- 数据库表和列来自 AI 元数据白名单，并经过 PostgreSQL 标识符格式校验；
- 过滤值全部使用参数绑定；
- `limit`、操作符、排序方向和字段操作权限均由程序校验；
- `AI_QUERY_*` 密码和 LLM/Embedding Key 只能放在本机 Git 忽略配置中；
- 真实 API Key 曾经出现在聊天记录或日志中时，应立即撤销并重新生成。
