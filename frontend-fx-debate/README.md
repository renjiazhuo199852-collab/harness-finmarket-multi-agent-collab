# FX Debate Workspace

独立于 `agent/fx_debate_test_ui` 和 `Vibe-Trading/frontend` 的 React 工作区。生产实时通信使用 Vibe Session/SSE；旧版 `/api/runs` 轮询前端不受影响。

## 启动

```bash
cd harness-finmarket-multi-agent-collab/frontend-fx-debate
npm install
npm run dev
```

入口：`http://127.0.0.1:5898/`

默认代理到 `http://127.0.0.1:8899`。如后端地址不同：

```bash
VITE_API_URL=http://127.0.0.1:8899 npm run dev
```

## 视图

```text
/?view=chat
/?view=canvas
/?view=data
/?view=logs
/?view=report
/?view=settings
```

Session 和运行 ID 会自动写入 URL，刷新后会从 Session 消息和 `/swarm/runs/{id}` 补全状态。

“设置”页面支持配置和测试工作区后端、模型供应商以及后续行情数据服务地址。连接参数保存在当前浏览器；点击保存后，模型参数和 Tushare Token 会通过 `/settings/*` 同步到服务端运行时配置。模型供应商测试只访问只读 `/models` 接口。

## 校验

```bash
npm run test:run
npm run build
```
