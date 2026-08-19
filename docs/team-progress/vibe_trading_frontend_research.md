# Vibe Trading 前端与 FX Debate 集成研究

## 结论

Vibe Trading 的 `/agent` 页面已经是会话、消息、SSE 重连和 Swarm 状态的主入口。FX Debate 不应继续维护一套独立的聊天请求和实时轮询页面，而应把当前控制台中的画布、证据、日志和报告提炼成一个可插拔的研究工作区。

## 一手源码证据

- `Vibe-Trading/frontend/src/components/layout/Layout.tsx:20-53,76-260`：全局布局负责导航、会话列表、会话删除/重命名、主题切换和 SSE 连接状态；`/agent?session=<id>` 是已有的会话入口。
- `Vibe-Trading/frontend/src/pages/Agent.tsx:213-273,442-690`：Agent 页面使用 `useSearchParams`、`useAgentStore` 和 `useSSE`；会话消息、流式文本、Tool 状态和 Swarm 状态都在同一页面生命周期内维护。
- `Vibe-Trading/frontend/src/pages/Agent.tsx:859-935`：发送消息时复用 `createSession`、`sendMessage`，并把会话 id 写入 URL；这应成为 FX Debate 的生产调用路径。
- `Vibe-Trading/frontend/src/hooks/useSSE.ts:27-177`：已有 Last-Event-ID、LRU 去重、断线重连和会话事件订阅，不应在新工作区重复实现。
- `Vibe-Trading/frontend/src/lib/api.ts:80-160`：同时存在会话接口和 `/swarm/runs` 查询/取消/重试接口，适合“会话实时流 + Swarm 详情补全”的两层读取模型。
- `Vibe-Trading/frontend/src/lib/swarmStatus.ts:86-230`：已有纯函数式 Swarm 状态构建器和事件 reducer，可作为通用适配器的基础。
- `Vibe-Trading/frontend/src/components/chat/SwarmStatusCard.tsx:86-176`：聊天中已经能显示 preset、运行状态、完成数、层级、Agent、Tool、耗时、迭代次数和输出摘要。
- `harness-finmarket-multi-agent-collab/agent/src/tools/swarm_tool.py:716-831`：SwarmTool 已通过 `event_callback` 把 `swarm.started` 和 `swarm.event` 转发到宿主会话 SSE；这是连接 FX Debate 与 Vibe Chat 的现成接缝。
- `harness-finmarket-multi-agent-collab/agent/src/api/swarm_routes.py:82-165,167-209`：已有 Swarm preset、运行详情和独立 SSE；生产工作区可用详情接口做刷新/回放，实时期间优先使用会话 SSE，避免双重订阅。
- `harness-finmarket-multi-agent-collab/agent/fx_debate_test_ui/app.js:980-1057,1189-1385`：当前独立控制台把 `/api/runs`、事件轮询、事件分类和 DOM 渲染绑在一起；其中事件分类、状态 reducer、结构化输出格式化可以迁移，页面请求和 DOM 绑定不应直接复制到 Vibe。

## 兼容约束

1. Vibe 的 Session/Message/Attempt 和 session SSE 是聊天的唯一事实来源。
2. `run_swarm` 的 `swarm.started`、`swarm.event` 事件名保持不变；新增 FX 字段必须可选并带版本号。
3. `/swarm/runs/{run_id}` 继续承担历史回放和刷新，不让浏览器在同一运行中同时消费两条实时流。
4. 当前 `/api/runs` 独立接口保留为本地测试 Adapter，不作为生产前端协议。
5. Event payload 中的输入、Tool 参数和数据库结果必须由后端脱敏；前端只渲染允许展示的字段。
