# FX Debate 外汇多智能体研究工作台

FX Debate 是一个面向金融研究人员的本地外汇研究与协作平台。系统把自然语言问题、受控数据检索、多智能体分析、辩论复核、风险审查和报告展示串成一条可追踪的研究链路。

## 我们的内容

### FX Debate 研究流程

一个完整的 FX Debate 运行包含 7 个智能体节点：

1. 宏观与技术分析师：整理宏观状态与 1D/4H 技术状态。
2. 多头观点分析师：尝试证伪 EUR/USD 上涨假设。
3. 空头观点分析师：尝试证伪 EUR/USD 下跌假设。
4. 多头辩论代理：回应空头反证，保留或让步于多头论点。
5. 空头辩论代理：回应多头反证，保留或让步于空头论点。
6. 外汇风险分析师：审查证据、风险、失效条件和允许动作。
7. 辩论裁决与外汇组合经理：综合前置分析和辩论结果，输出最终决策。

前 3 个研究节点并行执行，随后进入多空辩论、风险复核和最终裁决。协作画布会根据服务端返回的任务依赖动态生成，辩论节点在界面中作为一个辩论阶段突出展示。

### MCP 数据链

数据链路为：

```text
SSH 隧道
  -> MCP stdio unified_search
  -> AI Search 数据服务
  -> FX API
  -> FX Debate 前端
```

每次 FX 查询都会创建证据上下文并记录调用链。启动脚本会先执行 MCP 初始化、工具握手和真实数据烟测；MCP 不可用时默认停止后续服务，避免出现“页面在线但数据链断开”的假状态。

当前默认数据源是 AI Search/MCP。Excel 只作为显式配置的数据源，不会在 MCP 失败时静默替换。

### Agent Center

智能体中心支持按角色查看和维护 agent 配置：

- 输入中文自然语言修改方向，例如“增加数据时效审查和事件风险提示”。
- 模型生成 prompt/skill 候选方案、差异和审核结果。
- 用户可以手动修改候选方案，确认后再应用。
- 支持继续修改、查看版本历史、刷新配置和恢复默认。
- 配置只影响新运行；已开始的运行使用原配置快照。
- 工具白名单、任务依赖和平台安全规则不能通过编辑器修改。

### 工作区页面

- 对话：输入研究问题或直接查询行情。
- 智能体中心：浏览团队、启动指定 swarm、编辑 agent 配置。
- 协作画布：查看动态任务依赖、执行层和 agent 状态。
- 数据概览：查看本次运行的数据上下文和证据覆盖。
- 流程日志：查看 Agent、Tool、MCP、SDK、Database 事件。
- 最终报告：查看辩论裁决全文和四类研究节点报告。
- 设置：配置模型、API 地址和数据服务连接。

### 两种问题入口

启动研究辩论：

```text
分析 EURUSD 未来两周走势。
```

查询最新汇率（不启动 Debate）：

```text
查询美元兑欧元最新汇率
```

### 报告输出

最终报告保留：

- 辩论裁决全文；
- 四类研究节点的完整报告；
- 数据限制、证据引用和审计段落；
- 机器可读结果（仅在下载文件中提供）。

报告支持下载 Markdown、HTML，并可通过浏览器打印或保存为 PDF。

## 启动方式

### 推荐：一键启动

在项目目录执行：

```bash
cd /Users/xiaoranguo/Documents/ZJU/project/ICBC_intern/harness-finmarket-multi-agent-collab
./start_fx_debate.sh
```

如果从外层项目目录启动：

```bash
cd /Users/xiaoranguo/Documents/ZJU/project/ICBC_intern
FRONTEND_PORT=5899 ./start_fx_debate.sh
```

脚本依次执行：

1. 检查并关闭脚本管理的旧服务；
2. 建立 SSH 数据库隧道；
3. 启动 MCP stdio 并执行 `unified_search` 前置检查；
4. 启动 AI Search HTTP 服务；
5. 启动 FX API；
6. 启动 FX Debate 前端并执行健康检查。

SSH 使用密码认证时，密码会在当前终端交互式输入，不会写入脚本或日志。

### 常用命令

```bash
./start_fx_debate.sh status
./start_fx_debate.sh stop
./start_fx_debate.sh version
FRONTEND_PORT=5899 ./start_fx_debate.sh
```

### 服务端口

| 服务 | 默认地址 | 说明 |
| --- | --- | --- |
| FX Debate 前端 | `http://127.0.0.1:5898/` | 设置 `FRONTEND_PORT=5899` 可改为 5899 |
| FX API | `http://127.0.0.1:8899/` | Session、SSE、Swarm、报告和设置接口 |
| AI Search | `http://127.0.0.1:8011/health` | MCP 数据服务的 HTTP 健康检查 |
| SSH 转发 | `127.0.0.1:15433` | 远程数据服务数据库端口 |

MCP stdio 不需要单独常驻。启动时会完成一次握手，FX API 在实际查询时按相同配置按需建立 MCP 会话。

## 配置

复制配置模板：

```bash
cd /Users/xiaoranguo/Documents/ZJU/project/ICBC_intern/harness-finmarket-multi-agent-collab
cp agent/.env.example agent/.env
```

在 `agent/.env` 中配置聊天模型和 API 地址。真实 API key 只放在本机环境变量或 dotenv 文件中，不要提交到 Git。

默认要求 MCP 数据链可用：

```bash
FX_DEBATE_DATA_SOURCE=ai_search
MCP_REQUIRED=1
```

只有在明确需要旧 Excel 数据源时才显式切换：

```bash
FX_DEBATE_DATA_SOURCE=excel
MCP_REQUIRED=0
```

AI Search 的数据库凭据、Embedding 模型和 MCP 服务端私有配置由 `external/market-data-tools/` 管理，详见 [`README_FX_DEBATE.md`](README_FX_DEBATE.md)。

## 项目结构

```text
agent/
  src/fx_debate/       外汇路由、证据、分析和契约
  src/swarm/           preset、任务依赖和运行时
  src/tools/           FX、MCP、报告和校验工具
  src/skills/          agent 专业技能
frontend-fx-debate/    React 前端工作区
external/market-data-tools/
                       AI Search 与 MCP 数据服务
start_fx_debate.sh     一键启动脚本
README_FX_DEBATE.md    详细开发、联调和故障排查文档
```

## 验证与排障

查看启动版本和进程状态：

```bash
./start_fx_debate.sh version
./start_fx_debate.sh status
```

MCP 前置检查失败时，优先查看：

```text
.runtime/logs/mcp-preflight.log
```

常见原因包括 SSH 隧道未建立、远程数据库端口未监听、MCP 服务依赖缺失或 `unified_search` 查询不在数据目录中。修复原因后重新运行一键启动脚本即可。

后端测试和前端构建命令见 [`README_FX_DEBATE.md`](README_FX_DEBATE.md)。

## 安全边界

本系统用于研究和决策辅助，不自动执行实盘下单。证据必须来自当前运行的数据上下文；模型不能通过 agent prompt 修改工具白名单、任务依赖或安全规则。请勿提交 `.env`、API key、SSH 私钥和运行产物。
