# Phase 2 市场数据 Tool 团队使用说明

## 1. 目的与范围

本说明面向已拉取本仓库 `main` 分支的团队成员，说明 Phase 2 PostgreSQL
市场数据如何接入本地 Agent，以及如何验证四个内部只读 Tool。

本次功能已随 PR #10 合并到 `main`。它读取团队内部 PostgreSQL 的 Phase 2
市场数据，不负责采集 LSEG 数据、不向数据库写入报价或新闻，也不替代项目已有的
SQLite 本地会话和策略存储。

四个 Tool 如下：

| Tool | 用途 | 正式查询关系 |
| --- | --- | --- |
| `get_market_bars` | 查询历史 OHLCV K 线 | `instrument_master -> market_bars` |
| `get_latest_prices` | 查询当前最新报价快照 | `instrument_master -> latest_prices` |
| `get_macro_observations` | 查询与工具正式关联的宏观指标发布数据 | `instrument_master -> instrument_metric_link -> macro_observations` |
| `get_news` | 查询与工具正式关联的新闻 | `instrument_master -> news_instrument_link -> news_articles` |

例如，用户输入 `EURUSD` 时，程序先解析为内部 `instrument_id`，再按上表的关系
读取数据。供应商 RIC 仅用于追溯上游代码，不作为内部业务关联的主键。

## 2. 本次项目改动

本次改动已经进入仓库 `main`，主要文件及职责如下：

| 文件 | 职责 |
| --- | --- |
| `agent/src/config/env_schema.py` | 定义可选 `MARKET_DB_*` 本机环境配置，并校验端口、超时和必填项。 |
| `agent/src/market_database.py` | PostgreSQL 只读连接边界：懒加载驱动、参数化 SQL、只读事务、连接超时和语句超时。 |
| `agent/src/market_data_reader.py` | 四条业务查询路径、标准代码解析、参数校验及结果组织。 |
| `agent/src/tools/internal_market_data_tools.py` | 四个 Agent Tool 的 JSON Schema、统一 JSON 输出和错误处理。 |
| `agent/mcp_server.py` | 四个同名 MCP 包装函数，供 MCP 客户端调用。 |
| `agent/tests/test_market_database.py` | 数据库连接层单元测试。 |
| `agent/tests/test_market_data_reader.py` | 四条查询路径、关联关系及输入校验单元测试。 |
| `agent/tests/test_internal_market_data_tools.py` | Tool 注册、参数转发和 JSON 输出单元测试。 |
| `agent/scripts/verify_market_data_tools.py` | 手工真实数据库验证脚本，不进入 CI，也不调用 LLM。 |

数据库 Tool 仅在 `MARKET_DB_ENABLED=1` 且全部 `MARKET_DB_*` 必填配置完整时注册。
未配置数据库的普通开发环境不会看到这四个 Tool，也不会尝试连接 PostgreSQL。

## 3. 使用前提

每位成员需要自行具备以下内容：

1. 最新的团队仓库代码。
2. Python 3.11 或更高版本，以及 Windows OpenSSH 客户端。
3. 自己的 LLM Provider API Key，例如 DeepSeek API Key。
4. 团队管理员单独分配的 SSH 登录凭据和 PostgreSQL **只读**账号。
5. 已部署 Phase 2 表和样例/正式数据的 PostgreSQL 服务器。

不要共享 `root` 密码、SSH 私钥、数据库密码或 API Key。不要把这些值写进代码、
截图、Issue、PR、聊天记录或 Git 提交。

## 4. 首次安装

以下命令以 Windows PowerShell 为例，在仓库根目录执行：

```powershell
git switch main
git pull origin main

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[market-db,dev]"
```

命令含义：

```text
.venv
  项目专用 Python 虚拟环境，不提交 Git。

pip install -e ".[market-db,dev]"
  安装当前仓库代码、PostgreSQL 驱动 psycopg 和 pytest/ruff 等开发工具。
  -e 表示 editable：本机修改源码后无需再次安装即可运行。
```

若要使用 DeepSeek 的原生适配器，可额外安装：

```powershell
pip install -e ".[market-db,deepseek,dev]"
```

使用 OpenAI 兼容模式的 DeepSeek 时，基础依赖中的 `langchain-openai` 已足够，
不要求安装 `deepseek` 可选依赖。

## 5. 建立 SSH 隧道

数据库当前只监听服务器本机地址。成员需要在自己的电脑建立 SSH 隧道，将本机端口
转发到服务器 PostgreSQL 端口。

另开一个 PowerShell 窗口，使用团队管理员分配的个人 SSH 账号执行：

```powershell
ssh -N -p <SSH_PORT> -L 15433:127.0.0.1:5433 <SSH_USER>@<SSH_HOST>
```

参数含义：

```text
15433                 成员电脑上的本机端口
127.0.0.1:5433        SSH 服务器视角下的 PostgreSQL 地址
<SSH_USER>@<SSH_HOST> 个人 SSH 凭据，不使用 root 共享密码
-N                    只建立转发，不打开远程 Shell
```

保持该窗口运行。关闭 SSH 进程后，`127.0.0.1:15433` 将不再连接到 PostgreSQL。
如果本机 `15433` 已被占用，可换一个空闲端口，同时在 `agent/.env` 中同步修改
`MARKET_DB_PORT`。

## 6. 创建本机配置

在仓库根目录执行：

```powershell
Copy-Item agent\.env.example agent\.env
```

编辑本机 `agent/.env`。这是 Git 忽略文件，仅保留在成员电脑，至少填写以下内容：

```text
# 下面以 DeepSeek 的 OpenAI 兼容模式为例；密钥由成员自己申请。
LANGCHAIN_PROVIDER=deepseek
LANGCHAIN_MODEL_NAME=<团队选择的模型名>
DEEPSEEK_API_KEY=<个人 DeepSeek API Key>
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
VIBE_TRADING_DEEPSEEK_ADAPTER=openai-compatible

# Phase 2 内部 PostgreSQL。HOST/PORT 对应第 5 节建立的本机 SSH 隧道。
MARKET_DB_ENABLED=1
MARKET_DB_HOST=127.0.0.1
MARKET_DB_PORT=15433
MARKET_DB_NAME=<数据库名>
MARKET_DB_USER=<个人 PostgreSQL 只读账号>
MARKET_DB_PASSWORD=<数据库密码>
MARKET_DB_CONNECT_TIMEOUT_SECONDS=5
MARKET_DB_STATEMENT_TIMEOUT_MS=10000
```

安全要求：

- `agent/.env` 已被 `.gitignore` 排除，不应执行 `git add -f agent/.env`。
- 每位成员使用自己的 API Key，方便额度管理和密钥撤销。
- PostgreSQL 账号应只授予目标表的 `SELECT` 权限；应用代码的只读事务不能替代数据库权限。
- 若密钥曾出现在聊天、截图或提交中，应立即在对应控制台撤销并重新生成。

## 7. Tool 输入与输出

四个 Tool 的统一输出信封如下：

```json
{"ok": true, "data": {"count": 1}}
```

参数校验、数据库未配置、工具代码不存在等可预期错误会返回：

```json
{"ok": false, "error": "错误说明"}
```

### 7.1 `get_market_bars`

必填：`symbol`。可选：`source`、`frequency`、`start_date`、`end_date`、`limit`。

```json
{
  "symbol": "EURUSD",
  "source": "LSEG",
  "frequency": "daily",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "limit": 250
}
```

返回 `instrument`、`bars` 和 `count`。每条 K 线包含 `bar_date`、`bar_time`、
`open`、`high`、`low`、`close`、`volume`、`source` 和供应商原始代码字段。
`limit` 范围为 1 到 1000。

### 7.2 `get_latest_prices`

必填：`symbol`。可选：`source`。

```json
{
  "symbol": "EURUSD",
  "source": "LSEG"
}
```

返回 `instrument`、`prices` 和 `count`。每条报价包含 `price_time`、`last_price`、
`bid`、`ask`、`mid_price` 和供应商原始代码字段。它是当前快照，不是历史价格序列。

### 7.3 `get_macro_observations`

必填：`symbol`。可选：`metric_ids`、`source`、`start_date`、`end_date`、`limit`。

```json
{
  "symbol": "EURUSD",
  "metric_ids": ["US_INFLATION_CPI_YOY", "US_GROWTH_GDP_YOY"],
  "source": "LSEG",
  "limit": 100
}
```

返回 `instrument`、`observations` 和 `count`。记录包含 `relationship_role`、
`metric_id`、指标描述、发布日期、实际值、前值、预测值、修订值、国家和单位。
Tool 只返回 `instrument_metric_link` 中已经正式登记给该工具的指标；它不会按关键词
猜测宏观关联。`limit` 范围为 1 到 500。

### 7.4 `get_news`

必填：`symbol`。可选：`source`、`start_date`、`end_date`、`limit`。

```json
{
  "symbol": "EURUSD",
  "source": "LSEG",
  "limit": 50
}
```

返回 `instrument`、`articles` 和 `count`。文章包含 `article_id`、发布时间、标题、
正文、摘要、链接、语言、情绪分数、相关度分数及 `keywords`。Tool 通过
`news_instrument_link` 内连接判断正式关联，不以 JSONB `keywords` 猜测关联。
`limit` 范围为 1 到 200。

## 8. 启动与使用

激活虚拟环境、保持 SSH 隧道和配置文件就绪后，在仓库根目录执行：

```powershell
vibe-trading serve --port 8899
```

然后在本机浏览器打开 `http://localhost:8899`，例如提出：

```text
请查询 EURUSD 的日 K 线、LSEG 最新报价、正式关联的宏观数据和正式关联新闻。
请使用 get_market_bars、get_latest_prices、get_macro_observations、get_news。
```

也可从命令行做单次 Agent 验证：

```powershell
vibe-trading run -p "请查询 EURUSD 的日 K 线、LSEG 最新报价、正式关联宏观数据和正式关联新闻，并调用四个内部市场数据 Tool。"
```

模型是否调用全部四个 Tool 取决于用户问题和 Agent 提示；上面的测试提示明确要求使用四个
Tool，适合验证注册、Function Calling 和结果回传链路。

## 9. 如何复刻测试

### 9.1 单元测试：无需网络和密钥

以下三个测试文件已经提交到 `main`，任何成员安装 `dev` 依赖后均可运行：

```text
agent/tests/test_market_database.py
agent/tests/test_market_data_reader.py
agent/tests/test_internal_market_data_tools.py
```

在仓库根目录、已激活 `.venv` 的 PowerShell 中执行：

```powershell
python -m pytest `
  agent/tests/test_market_database.py `
  agent/tests/test_market_data_reader.py `
  agent/tests/test_internal_market_data_tools.py -q
```

预期结果：

```text
19 passed
```

这些测试使用假的数据库客户端记录 SQL 和绑定参数，不访问 SSH、真实 PostgreSQL、
真实密码或任何 LLM API。验证内容包括：

| 测试对象 | 验证内容 |
| --- | --- |
| `MarketDatabaseClient` | 未配置不连接；参数绑定；连接关闭；只读事务；连接异常转换。 |
| `MarketDataReader` | `EURUSD -> FX000001`；K 线按 `instrument_id`；宏观经过 `instrument_metric_link`；新闻经过 `news_instrument_link`；参数校验和未知代码错误。 |
| 四个 Agent Tool | Tool 注册开关；输入转发；日期与 `Decimal` JSON 序列化；稳定错误 JSON。 |

### 9.2 真实 PostgreSQL Tool 验证：无需调用 LLM

完成第 4 至 6 节后，运行新增的手工验证脚本：

```powershell
python agent/scripts/verify_market_data_tools.py `
  --symbol EURUSD `
  --source LSEG `
  --require-data
```

脚本会从 `agent/.env` 加载本机配置，依次调用四个真实 Agent Tool，并输出每个 Tool
的成功状态和返回记录数。`--require-data` 表示任何 Tool 返回 0 行时也视为失败，适合
验证当前 Phase 2 样例库。

2026-07-29 的样例数据验证结果为：

| Tool | 预期最少记录数 | 当时实际记录数 | 关键结果 |
| --- | ---: | ---: | --- |
| `get_market_bars` | 1 | 1 | `EUR=` 日 K 线，收盘价 `1.1030`。 |
| `get_latest_prices` | 1 | 1 | `bid=1.1032`、`ask=1.1036`。 |
| `get_macro_observations` | 1 | 3 | 美国 CPI、GDP、联邦基金利率。 |
| `get_news` | 1 | 1 | `story-001`，ECB 相关新闻。 |

随着正式数据持续导入，精确数量和数值会变化；脚本只要求查询成功，`--require-data` 时
只要求每类数据至少有一条，而不把样例库数量写死。

如需查看完整 Tool JSON，请增加 `--show-data`：

```powershell
python agent/scripts/verify_market_data_tools.py --show-data
```

### 9.3 真实 AgentLoop 端到端验证：会消耗 API Key 额度

本验证确认真实模型能识别 Tool Schema、主动请求 Function Call、接收 PostgreSQL 查询结果
并生成最终回答。前提是 SSH 隧道、`MARKET_DB_*`、LLM Provider 配置和 API Key 都可用。

执行第 8 节的 `vibe-trading run -p` 命令，并检查运行日志或最终回答是否体现：

```text
get_market_bars
  -> 成功返回 K 线
get_latest_prices
  -> 成功返回最新报价
get_macro_observations
  -> 成功返回正式关联宏观指标
get_news
  -> 成功返回正式关联新闻
```

这不是 CI 测试，原因是它需要个人密钥、可达的 SSH 隧道和真实数据库，还会产生模型
调用费用。它验证完整运行链路；单元测试则负责稳定、免费地验证代码逻辑。

## 10. 故障排查

| 现象 | 排查方式 |
| --- | --- |
| Agent 中完全看不到四个 Tool | 检查 `MARKET_DB_ENABLED=1`，并确认 `HOST`、`NAME`、`USER`、`PASSWORD` 都不为空；修改 `.env` 后重启 Agent。 |
| 提示未安装 PostgreSQL 支持 | 在激活的虚拟环境运行 `pip install -e ".[market-db,dev]"`。 |
| 连接超时或连接被拒绝 | 确认 SSH 隧道窗口仍在运行，并确认 `.env` 中端口与 `ssh -L` 左侧端口一致。 |
| 数据库认证失败 | 向管理员确认个人数据库只读账号、密码、数据库名和授权范围；不要改用 root。 |
| Tool 返回 0 条 | 确认工具代码、来源、日期筛选和 Phase 2 关系数据已导入；`limit` 只是上限，不会补造数据。 |
| Agent 没有调用全部四个 Tool | 用第 8 节的明确测试提示；模型会根据问题决定调用哪些 Tool。 |

## 11. 当前边界和后续维护

1. 四个 Tool 只读内部 PostgreSQL，不承担 LSEG 采集、报价 UPSERT、新闻关联写入或宏观关联写入。
2. `get_latest_prices` 是当前快照；历史价格应使用 `get_market_bars`。
3. 宏观关联由 `instrument_metric_link` 维护；新闻关联由 `news_instrument_link` 维护。新增工具、指标或新闻时，数据维护流程必须同步维护相应关系表。
4. 所有成员应使用独立 SSH 和数据库只读凭据。长期可考虑内网/VPN 或统一只读 API，以减少每台开发机维护 SSH 隧道的成本。
5. 该说明、单元测试和手工验证脚本可提交 Git；任何真实 `.env`、密钥、数据库导出和运行产物均不得提交。
