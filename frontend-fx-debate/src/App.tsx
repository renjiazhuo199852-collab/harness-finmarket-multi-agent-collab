import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import {
  Activity, AlertCircle, ArrowUpRight, CheckCircle2, ChevronRight, CircleDot, Database,
  FileText, History, ListTree, MessageSquare, Moon, Network, PanelLeft, PanelLeftClose,
  Plus, RefreshCw, Send, Server, Settings, Square, Sun, XCircle,
} from "lucide-react";
import { api } from "@/lib/api";
import { MarkdownContent } from "@/components/MarkdownContent";
import { SettingsView } from "@/components/SettingsView";
import { SessionTransport, type SSEStatus } from "@/lib/sse";
import { buildResearchProgress, type ProgressStageStatus, type ResearchProgressStage } from "@/lib/progress";
import { activeSnapshot, applyRunEvent, emptyRunWorkspace, hydrateRunSnapshot, markActiveRunCancelled, replaceRunSummaries, runIdFromEvent, selectRun as selectRunState } from "@/lib/run_workspace";
import { isRunActive, settleCancellation } from "@/lib/run_controls";
import type { AgentSnapshot, DebateRunSummary, MessageItem, SessionEvent, SessionItem, WorkspaceEvent, WorkspaceSnapshot, WorkspaceView } from "@/types";
import "@/styles.css";

const VIEW_LABELS: Record<WorkspaceView, string> = {
  chat: "对话",
  canvas: "协作画布",
  data: "数据概览",
  logs: "流程日志",
  report: "最终报告",
  settings: "设置",
};

const VIEW_ICONS: Record<WorkspaceView, typeof MessageSquare> = {
  chat: MessageSquare,
  canvas: Network,
  data: Database,
  logs: ListTree,
  report: FileText,
  settings: Settings,
};


function readView(): WorkspaceView {
  const view = new URLSearchParams(window.location.search).get("view");
  return view && view in VIEW_LABELS ? view as WorkspaceView : "chat";
}

function updateUrl(values: Record<string, string | undefined>): void {
  const params = new URLSearchParams(window.location.search);
  Object.entries(values).forEach(([key, value]) => value ? params.set(key, value) : params.delete(key));
  window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
}

const HISTORY_EPOCH_KEY = "fx-debate-history-started-at-v2";

function readHistoryEpoch(): number {
  const stored = Number(localStorage.getItem(HISTORY_EPOCH_KEY));
  if (Number.isFinite(stored) && stored > 0) return stored;
  const now = Date.now();
  localStorage.setItem(HISTORY_EPOCH_KEY, String(now));
  return now;
}

function visibleSession(session: SessionItem, epoch: number): boolean {
  const created = Date.parse(session.created_at);
  return !Number.isNaN(created) && created >= epoch;
}

function sessionTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return date.toLocaleDateString([], { month: "2-digit", day: "2-digit" });
}

function sessionStatusText(status: string): string {
  const labels: Record<string, string> = {
    active: "进行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    archived: "已归档",
  };
  return labels[status] || status;
}

function SessionSidebar({
  sessions,
  activeSessionId,
  onNew,
  onSelect,
  onReset,
}: {
  sessions: SessionItem[];
  activeSessionId: string;
  onNew: () => void;
  onSelect: (sessionId: string) => void;
  onReset: () => void;
}): ReactElement {
  return <aside className="session-sidebar">
    <div className="sidebar-heading"><div><span className="eyebrow">SESSIONS</span><h2>对话历史</h2></div><button className="sidebar-new" onClick={onNew} title="新建对话"><Plus size={17} /></button></div>
    <button className="new-session-button" onClick={onNew}><Plus size={15} />新建对话</button>
    <div className="session-list">
      {sessions.length === 0 ? <div className="sidebar-empty"><History size={18} /><span>暂无新的对话</span><small>发送第一个问题后会显示在这里</small></div> : sessions.map((session) => <button key={session.session_id} className={`session-item ${activeSessionId === session.session_id ? "session-active" : ""}`} onClick={() => onSelect(session.session_id)}>
        <span className="session-item-title">{session.title || "FX Debate"}</span>
        <span className="session-item-meta"><span>{sessionTime(session.updated_at || session.created_at)}</span><span className={`session-status session-status-${session.status}`}>{sessionStatusText(session.status)}</span></span>
      </button>)}
    </div>
    <button className="reset-history-button" onClick={onReset}><RefreshCw size={13} />重新开始，隐藏旧历史</button>
  </aside>;
}

function json(value: unknown): string {
  if (value === undefined || value === null || value === "") return "-";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function time(value?: string): string {
  if (!value) return "--:--:--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], { hour12: false });
}

function statusText(status: string): string {
  const labels: Record<string, string> = {
    pending: "待运行", running: "运行中", retrying: "重试中", in_progress: "执行中", completed: "已完成", failed: "失败",
    blocked: "等待依赖", cancelled: "已取消", idle: "等待输入",
  };
  return labels[status] || status;
}

function visibleAgentStatus(status: string, runStatus: WorkspaceSnapshot["status"]): string {
  return runStatus === "running" && status === "failed" ? "retrying" : status;
}

function visibleEventStatus(event: WorkspaceEvent, runStatus: WorkspaceSnapshot["status"]): string {
  const terminalFailure = ["task_failed", "run_failed", "run_error", "task.failed", "run.failed", "run.error"].includes(event.type);
  return runStatus === "running" && terminalFailure ? "retrying" : event.status || "running";
}

function compactText(value: string, limit = 240): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `…${normalized.slice(-limit)}` : normalized;
}

function completedOutputPreview(value: string, limit = 240): string {
  const prose = value
    .replace(/```(?:json)?[\s\S]*?```/gi, " ")
    .replace(/^>.*$/gm, " ")
    .replace(/^#{1,6}\s+.*$/gm, " ")
    .replace(/\[[^\]]+\]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!prose) return "该角色已完成研究，完整结论可在协作画布中查看。";
  return prose.length > limit ? `${prose.slice(0, limit)}…` : prose;
}

function durationText(elapsedMs?: number): string {
  if (!elapsedMs) return "";
  if (elapsedMs < 1000) return `${Math.round(elapsedMs)} ms`;
  const seconds = elapsedMs / 1000;
  return seconds < 60 ? `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒` : `${Math.floor(seconds / 60)}分${Math.round(seconds % 60)}秒`;
}

function friendlyError(value?: string): string {
  if (!value) return "";
  if (value.includes("cancelled by user")) return "本次研究已停止，可以继续发送新的问题";
  if (value.includes("insufficient_quota") || value.includes("额度已用完")) return "LLM API 额度不足，任务已停止";
  if (value.includes("HTTP 502") || value.includes("Bad gateway")) return "LLM 服务网关暂时不可用（HTTP 502），本次研究未能启动";
  if (value.includes("UNEXPECTED_EOF") || value.includes("ConnectError")) return "LLM 连接中断，任务未完成";
  return compactText(value, 180);
}

function StatusPill({ status }: { status: string }): ReactElement {
  const Icon = status === "completed" ? CheckCircle2 : status === "failed" ? XCircle : status === "running" || status === "retrying" || status === "in_progress" ? Activity : CircleDot;
  return <span className={`status-pill status-${status}`}><Icon size={13} />{statusText(status)}</span>;
}

function EventLayerPill({ layer }: { layer: string }): ReactElement {
  return <span className={`layer-pill layer-${layer.toLowerCase()}`}>{layer}</span>;
}

function EmptyState({ title, detail }: { title: string; detail: string }): ReactElement {
  return <div className="empty-state"><CircleDot size={24} /><strong>{title}</strong><span>{detail}</span></div>;
}

function ChatView({
  messages,
  streamingText,
  reasoning,
  workspace,
  onView,
  onSelectAgent,
}: {
  messages: MessageItem[];
  streamingText: string;
  reasoning: boolean;
  workspace: WorkspaceSnapshot;
  onView: (view: WorkspaceView) => void;
  onSelectAgent: (agent: AgentSnapshot) => void;
}): ReactElement {
  const hasSwarm = Boolean(workspace.runId);
  const progress = buildResearchProgress(workspace);
  return <div className="chat-layout">
    <div className="message-list">
      {messages.length === 0 && !streamingText && <div className="welcome">
        <div className="welcome-mark"><Network size={22} /></div>
        <h1>FX Debate Workspace</h1>
        <p>用自然语言发起 EURUSD 研究。对话保持 Vibe Trading 的节奏，协作过程会同步出现在工作区。</p>
        <div className="suggestions">
          <button onClick={() => onView("chat")}>分析 EURUSD 未来两周走势，并给出风险可控的交易建议 <ArrowUpRight size={15} /></button>
          <button onClick={() => onView("canvas")}>查看本次请求的处理链路 <ArrowUpRight size={15} /></button>
        </div>
      </div>}
      {messages.map((message) => <article key={message.message_id} className={`message ${message.role === "user" ? "message-user" : "message-assistant"}`}>
        <div className="message-meta"><span>{message.role === "user" ? "你" : "FX Debate"}</span><time>{time(message.created_at)}</time></div>
        <div className="message-body">{message.role === "assistant"
          ? message.content.startsWith("Execution failed:")
            ? <p className="message-error-text">{friendlyError(message.content)}</p>
            : <MarkdownContent>{message.content}</MarkdownContent>
          : message.content}</div>
      </article>)}
      {streamingText && <article className="message message-assistant streaming-message">
        <div className="message-meta"><span>FX Debate</span><span className="live-dot">● 实时</span></div>
        <div className="message-body"><MarkdownContent>{streamingText}</MarkdownContent></div>
      </article>}
      {reasoning && !streamingText && <div className="reasoning"><Activity size={14} /> Agent 正在整理证据和协作结果…</div>}
      <ResearchProgressPanel workspace={workspace} onView={onView} onSelectAgent={onSelectAgent} />
    </div>
    <div className="chat-summary">
      <div className="summary-heading"><span>当前运行</span><StatusPill status={workspace.status} /></div>
      {hasSwarm ? <>
        <div className="run-id">{workspace.preset || "FX Debate"}<code>{workspace.runId}</code></div>
        <div className="summary-agents">{workspace.agents.map((agent) => { const displayStatus = visibleAgentStatus(agent.status, workspace.status); return <div className="summary-agent" key={agent.id}><span className={`agent-dot dot-${displayStatus}`} /><span>{agent.role}</span><small>{statusText(displayStatus)}</small></div>; })}</div>
        <div className="summary-actions"><button className="text-button" onClick={() => onView("canvas")}>协作画布 <ChevronRight size={14} /></button><button className="text-button" onClick={() => onView("report")}>最终报告 <ChevronRight size={14} /></button></div>
      </> : workspace.status !== "idle" ? <div className="summary-current"><strong>{progress.currentLabel}</strong><p>{workspace.lastError ? friendlyError(workspace.lastError) : "正在等待服务端确定处理路径；收到运行计划后会自动显示实际任务和依赖关系。"}</p><button className="text-button" onClick={() => onView("logs")}>查看流程日志 <ChevronRight size={14} /></button></div> : <p className="muted">发送问题后，运行 ID、实际处理路径和证据摘要会显示在这里。</p>}
    </div>
  </div>;
}

const FX_AGENT_UI: Record<string, {
  title: string;
  subtitle: string;
  description: string;
  focus: string;
  icon: typeof Activity;
}> = {
  pair_bull: {
    title: "多头观点分析师",
    subtitle: "Pair Bull",
    description: "从价格结构和利多因素出发，寻找 EURUSD 上行的证据。",
    focus: "上行空间 · 支撑位",
    icon: ArrowUpRight,
  },
  pair_bear: {
    title: "空头观点分析师",
    subtitle: "Pair Bear",
    description: "主动寻找下跌风险和反例，避免只看单一方向。",
    focus: "下行风险 · 阻力位",
    icon: ArrowUpRight,
  },
  macro_technical: {
    title: "宏观与技术分析师",
    subtitle: "Macro + Technical",
    description: "把宏观新闻、经济数据和技术指标放到同一时间框架里交叉验证。",
    focus: "宏观驱动 · 趋势确认",
    icon: Activity,
  },
  debate_judge: {
    title: "辩论裁判 / 交易经理",
    subtitle: "Debate Judge / FX PM",
    description: "比较多空证据，给出主情景、备选情景和可执行的交易计划。",
    focus: "方向概率 · 交易计划",
    icon: CheckCircle2,
  },
  fx_risk_officer: {
    title: "外汇风险官",
    subtitle: "FX Risk Officer",
    description: "复核止损、止盈、持仓周期和失效条件，控制建议的最大风险。",
    focus: "止损止盈 · 失效条件",
    icon: AlertCircle,
  },
};

function agentUi(agent: AgentSnapshot) {
  return FX_AGENT_UI[agent.id] || {
    title: agent.role,
    subtitle: agent.id,
    description: "该研究角色正在等待服务端事件。",
    focus: "等待数据",
    icon: Activity,
  };
}

function ProgressIcon({ status }: { status: ProgressStageStatus }): ReactElement {
  if (status === "completed") return <CheckCircle2 size={15} />;
  if (status === "failed") return <XCircle size={15} />;
  if (status === "in_progress") return <Activity size={15} />;
  return <CircleDot size={15} />;
}

function ResearchProgressPanel({ workspace, onView, onSelectAgent }: { workspace: WorkspaceSnapshot; onView: (view: WorkspaceView) => void; onSelectAgent: (agent: AgentSnapshot) => void }): ReactElement | null {
  if (workspace.status === "idle" && workspace.events.length === 0) return null;
  const progress = buildResearchProgress(workspace);
  const visibleAgents = workspace.agents.filter((agent) => agent.output || agent.error || ["running", "retrying", "in_progress", "failed", "blocked"].includes(visibleAgentStatus(agent.status, workspace.status)));
  return <section className="research-progress" aria-label="研究执行进度">
    <div className="progress-head"><div><span>研究链路与实时进度</span><strong>{progress.currentLabel}</strong></div><StatusPill status={workspace.status} /></div>
    <div className="progress-stages">{progress.stages.map((stage, index) => <div className={`progress-stage progress-${stage.status}`} key={stage.id}>
      <div className="progress-stage-marker"><ProgressIcon status={stage.status} /><i /></div>
      <div><span>第 {index + 1} 步</span><strong>{stage.label}</strong><small>{stage.detail}</small></div>
    </div>)}</div>
    {visibleAgents.length > 0 && <div className="agent-stream-list">{visibleAgents.map((agent) => {
      const meta = agentUi(agent);
      const displayStatus = visibleAgentStatus(agent.status, workspace.status);
      const transientFailure = workspace.status === "running" && agent.status === "failed";
      const summary = transientFailure
        ? "服务端正在确认最终状态"
        : agent.error
        ? friendlyError(agent.error)
        : agent.tool ? `正在调用 ${agent.tool}`
        : displayStatus === "blocked" ? "等待前置任务完成" : statusText(displayStatus);
      return <article className="agent-stream" key={agent.id}><span className={`agent-dot dot-${displayStatus}`} /><div><div className="agent-stream-head"><strong>{meta.title}</strong><span>{durationText(agent.elapsedMs) || statusText(displayStatus)}</span></div><p>{summary}</p></div><button className="agent-detail-button" onClick={() => onSelectAgent(agent)}>查看详情 <ChevronRight size={13} /></button></article>;
    })}</div>}
    <div className="progress-actions"><button className="text-button" onClick={() => onView("canvas")}>查看协作画布 <ChevronRight size={14} /></button><button className="text-button" onClick={() => onView("logs")}>查看完整流程 <ChevronRight size={14} /></button></div>
  </section>;
}

function AgentCard({ agent, selected, onSelect, pendingText, workspaceStatus }: { agent: AgentSnapshot; selected: boolean; onSelect: () => void; pendingText?: string; workspaceStatus: WorkspaceSnapshot["status"] }): ReactElement {
  const meta = agentUi(agent);
  const Icon = meta.icon;
  const displayStatus = visibleAgentStatus(agent.status, workspaceStatus);
  const transientFailure = workspaceStatus === "running" && agent.status === "failed";
  const activity = agent.error
    ? transientFailure ? "服务端正在确认最终状态" : friendlyError(agent.error)
    : agent.tool ? `正在调用：${agent.tool}`
      : agent.output ? displayStatus === "completed" ? completedOutputPreview(agent.output, 150) : compactText(agent.output, 150)
        : (["pending", "blocked"].includes(displayStatus) ? pendingText || "等待上一阶段完成" : meta.description);
  return <button className={`agent-card ${selected ? "agent-selected" : ""}`} onClick={onSelect}>
    <div className="agent-card-top"><span className="agent-icon"><Icon size={16} /></span><div className="agent-card-title"><strong>{meta.title}</strong><span>{meta.subtitle}</span></div><StatusPill status={displayStatus} /></div>
    <p className="agent-card-description">{meta.description}</p>
    <div className="agent-card-detail"><span className={`agent-dot dot-${displayStatus}`} />{activity}</div>
    <div className="agent-card-foot"><span>{meta.focus}</span><span>{durationText(agent.elapsedMs) || statusText(displayStatus)}</span></div>
  </button>;
}

function ObservedStage({ stage, index }: { stage: ResearchProgressStage; index: number }): ReactElement {
  return <div className={`observed-stage progress-${stage.status}`}>
    <span className="observed-stage-icon"><ProgressIcon status={stage.status} /></span>
    <div><small>阶段 {index + 1}</small><strong>{stage.label}</strong><p>{stage.detail}</p></div>
  </div>;
}

function CanvasView({ workspace, onSelect, selectedAgentId }: { workspace: WorkspaceSnapshot; onSelect: (agent: AgentSnapshot) => void; selectedAgentId?: string }): ReactElement {
  const progress = buildResearchProgress(workspace);
  const executionStages = progress.stages.filter((stage) => stage.kind === "execution");
  const executionStart = progress.stages.findIndex((stage) => stage.kind === "execution");
  const executionEnd = progress.stages.map((stage) => stage.kind).lastIndexOf("execution");
  const beforeExecution = executionStart < 0 ? progress.stages : progress.stages.slice(0, executionStart);
  const afterExecution = executionEnd < 0 ? [] : progress.stages.slice(executionEnd + 1);
  const byId = new Map(workspace.agents.map((agent) => [agent.id, agent]));
  const taskById = new Map(workspace.tasks.map((task) => [task.id, task]));
  const target = workspace.variables.target || workspace.variables.symbol || "当前问题";
  const timeframe = workspace.variables.timeframe || workspace.variables.analysis_timeframes || workspace.variables.horizon || "未指定";
  const route = workspace.preset || (workspace.runId ? "协作运行" : "等待路由结果");
  const pendingMessage = (agent: AgentSnapshot): string => {
    const task = workspace.tasks.find((item) => item.id === agent.taskId || item.agent_id === agent.id);
    const dependencies = (task?.depends_on || []).map((taskId) => {
      const dependency = taskById.get(taskId);
      return dependency ? byId.get(dependency.agent_id)?.role || dependency.agent_id : taskId;
    });
    if (dependencies.length) return `等待前置任务：${dependencies.join("、")}`;
    return workspace.runId ? "任务已创建，等待服务端调度" : "等待服务端返回运行计划";
  };
  return <div className="workspace-view canvas-view">
    <div className="view-heading"><div><span className="eyebrow">DYNAMIC WORKFLOW</span><h2>本次请求的实际处理链路</h2><p>处理方向、角色数量和先后关系由服务端路由结果决定。点击角色可查看该节点真实收到和输出的信息。</p></div><StatusPill status={workspace.status} /></div>
    <div className="canvas-context"><div><span>研究对象</span><strong>{target}</strong></div><div><span>分析周期</span><strong>{timeframe}</strong></div><div><span>处理路径</span><strong>{route}</strong></div><div><span>当前阶段</span><strong>{progress.currentLabel}</strong></div><div><span>任务 / 事件</span><strong>{workspace.tasks.length} / {workspace.events.length}</strong></div></div>
    {beforeExecution.length > 0 && <div className="observed-flow" aria-label="运行准备阶段">{beforeExecution.map((stage, index) => <ObservedStage stage={stage} index={index} key={stage.id} />)}</div>}
    {executionStages.length === 0 ? <EmptyState title={workspace.status === "idle" ? "等待问题" : "等待服务端返回执行计划"} detail="这里不会预先放置固定 Agent；只有本次路由实际创建的任务才会出现在画布中。" /> : <div className="dynamic-dag" aria-label="服务端任务依赖图">
      {executionStages.map((stage, index) => <div className="dag-fragment" key={stage.id}>
        {index > 0 && <div className="dag-connector"><span>{executionStages[index - 1].status === "completed" ? "前置完成" : executionStages[index - 1].status === "failed" ? "前置失败" : "等待前层"}</span><ChevronRight size={18} /></div>}
        <section className={`dag-stage progress-${stage.status}`}>
          <div className="stage-heading"><div><span className="stage-kicker">执行层 {index + 1}</span><h3>{stage.label}</h3></div><StatusPill status={stage.status} /></div>
          <p className="dag-stage-detail">{stage.detail}</p>
          <div className="canvas-column">{stage.agentIds.map((agentId) => {
            const agent = byId.get(agentId);
            return agent ? <AgentCard key={agent.id} agent={agent} pendingText={pendingMessage(agent)} workspaceStatus={workspace.status} onSelect={() => onSelect(agent)} selected={selectedAgentId === agent.id} /> : null;
          })}</div>
        </section>
      </div>)}</div>}
    {afterExecution.length > 0 && <div className="observed-flow observed-flow-after" aria-label="运行收尾阶段">{afterExecution.map((stage, index) => <ObservedStage stage={stage} index={beforeExecution.length + executionStages.length + index} key={stage.id} />)}</div>}
    <div className="canvas-guide"><Server size={15} /><div><strong>画布数据来源</strong><span>只展示服务端本次返回的 preset、tasks、depends_on 以及实时事件；不同路由会自然形成不同的节点和阶段。</span></div></div>
  </div>;
}

function DataView({ workspace }: { workspace: WorkspaceSnapshot }): ReactElement {
  const groups = ["sdk", "database"] as const;
  const visibleItems = workspace.evidence.items.filter((item) => item.category === "sdk" || item.category === "database");
  return <div className="workspace-view">
    <div className="view-heading"><div><span className="eyebrow">DATA ACCESS</span><h2>数据调用</h2><p>只展示本次运行中的 SDK 和数据库调用结果；Agent 文本与普通流程事件请在流程日志中查看。</p></div><div className="context-tags"><span>{workspace.variables.target || workspace.variables.symbol || "当前请求"}</span><span>{workspace.variables.timeframe || "当前周期"}</span></div></div>
    {visibleItems.length === 0 ? <EmptyState title="暂无数据调用" detail="收到 SDK 或数据库返回后会显示在这里。" /> : <div className="evidence-groups">{groups.map((group) => {
      const items = visibleItems.filter((item) => item.category === group);
      if (!items.length) return null;
      return <section className="evidence-group" key={group}><h3>{group === "sdk" ? "SDK 调用" : "数据库调用"}</h3>{items.map((item) => <details className="evidence-item" key={item.id}><summary><span>{item.title}</span><small>{item.source || "内部调用"} · {item.asOf || "未标注时间"}</small></summary><p>{item.summary || "无摘要"}</p><pre>{json(item.raw)}</pre></details>)}</section>;
    })}</div>}
  </div>;
}

function LogsView({ events, runStatus, onSelect }: { events: WorkspaceEvent[]; runStatus: WorkspaceSnapshot["status"]; onSelect: (event: WorkspaceEvent) => void }): ReactElement {
  const [filter, setFilter] = useState("ALL");
  const filtered = filter === "ALL" ? events : events.filter((event) => event.layer === filter);
  return <div className="workspace-view logs-view">
    <div className="view-heading"><div><span className="eyebrow">EVENT STREAM</span><h2>流程日志</h2><p>完整调用链：Agent → Tool → SDK → Database。点击事件查看原始输入输出。</p></div><div className="filter-row">{["ALL", "AGENT", "TOOL", "SDK", "DATABASE", "SYSTEM"].map((value) => <button className={filter === value ? "filter-active" : ""} key={value} onClick={() => setFilter(value)}>{value === "ALL" ? "全部" : value}</button>)}</div></div>
    {filtered.length === 0 ? <EmptyState title="暂无流程事件" detail="事件会随 Session SSE 实时到达。" /> : <div className="event-table"><div className="event-row event-head"><span>时间</span><span>层级</span><span>Agent / 操作</span><span>状态</span><span>输入输出</span></div>{[...filtered].reverse().map((event) => <button className="event-row" key={event.id} onClick={() => onSelect(event)}><time>{time(event.timestamp)}</time><EventLayerPill layer={event.layer} /><span className="event-name"><strong>{event.label}</strong><small>{event.agentId || event.taskId || "系统"}</small></span><StatusPill status={visibleEventStatus(event, runStatus)} /><span className="event-open">查看 <ChevronRight size={14} /></span></button>)}</div>}
  </div>;
}

function ReportView({ report, workspace }: { report?: WorkspaceSnapshot["report"]; workspace: WorkspaceSnapshot }): ReactElement {
  if (!report) return <div className="workspace-view"><div className="view-heading"><div><span className="eyebrow">DECISION OUTPUT</span><h2>最终报告</h2><p>最终结果由本次路由选中的实际处理链路生成。</p></div></div><EmptyState title="报告尚未生成" detail={workspace.status === "failed" ? workspace.lastError || "本次运行失败，请查看流程日志。" : "等待当前处理链路完成。"} /></div>;
  return <div className="workspace-view report-view"><div className="view-heading"><div><span className="eyebrow">DECISION OUTPUT</span><h2>最终报告</h2><p>结构化字段优先；原始 Markdown 保留在下方。</p></div><StatusPill status={workspace.status} /></div>
    <div className="report-overview"><div className="decision-block"><span>方向判断</span><strong>{report.direction || "数据不足"}</strong><small>置信度：{report.confidence ?? "未提供"}</small></div><div className="decision-block"><span>交易动作</span><strong>{report.action || "数据不足"}</strong><small>{report.holdingPeriod || "未提供持仓周期"}</small></div>{report.probabilities && <div className="probability-block"><span>概率分布</span>{Object.entries(report.probabilities).map(([key, value]) => <div className="probability" key={key}><label>{key === "bullish" ? "看涨" : key === "bearish" ? "看跌" : "震荡"}<b>{value ?? "-"}</b></label><div><i style={{ width: `${Math.min(100, Number(value || 0) * (Number(value || 0) <= 1 ? 100 : 1))}%` }} /></div></div>)}</div>}</div>
    <div className="report-grid">{[["入场区间", report.entry], ["止损", report.stopLoss], ["止盈", report.takeProfit]].map(([label, value]) => <div className="report-field" key={label}><span>{label}</span><strong>{value || "数据不足"}</strong></div>)}</div>
    {report.rationale && <section className="report-section"><h3>核心依据</h3><ul>{report.rationale.map((item) => <li key={item}>{item}</li>)}</ul></section>}
    {report.invalidation && <section className="report-section"><h3>失效条件</h3><ul>{report.invalidation.map((item) => <li key={item}>{item}</li>)}</ul></section>}
    {report.risks && <section className="report-section risk-section"><h3>风险提示</h3><ul>{report.risks.map((item) => <li key={item}>{item}</li>)}</ul></section>}
    {report.markdown && <details className="raw-disclosure"><summary>查看原始报告文本</summary><pre>{report.markdown}</pre></details>}
  </div>;
}

function eventDisplayName(type: string): string {
  const labels: Record<string, string> = {
    worker_started: "Agent 开始工作",
    worker_failed: "临时异常，正在重试",
    worker_completed: "Agent 完成工作",
    task_retry: "任务重试",
    task_started: "任务开始",
    task_completed: "任务完成",
    "task.completed": "Agent 任务完成",
    "task.failed": "Agent 任务失败",
    "task.blocked": "Agent 任务阻塞",
    task_heartbeat: "执行状态更新",
    tool_call: "调用工具",
    tool_result: "工具返回结果",
    context_ready: "证据上下文已准备",
    historical_completed: "历史运行已完成",
  };
  return labels[type] || type.split("_").join(" ");
}

function DetailBlock({ label, value, markdown = false }: { label: string; value: unknown; markdown?: boolean }): ReactElement | null {
  if (value === undefined || value === null || value === "") return null;
  return <section className="detail-block"><h4>{label}</h4>{markdown && typeof value === "string" ? <div className="detail-markdown"><MarkdownContent>{value}</MarkdownContent></div> : <pre>{json(value)}</pre>}</section>;
}

function AgentDetail({ agent, events, workspaceStatus }: { agent: AgentSnapshot; events: WorkspaceEvent[]; workspaceStatus: WorkspaceSnapshot["status"] }): ReactElement {
  const meta = agentUi(agent);
  const Icon = meta.icon;
  const agentEvents = events.filter((event) => event.agentId === agent.id || (agent.taskId && event.taskId === agent.taskId));
  const displayStatus = visibleAgentStatus(agent.status, workspaceStatus);
  return <>
    <div className="agent-detail-hero"><span className="agent-icon"><Icon size={18} /></span><div><h3>{meta.title}</h3><span>{meta.subtitle} · {agent.id}</span></div><StatusPill status={displayStatus} /></div>
    <p className="agent-detail-intro">{meta.description}</p>
    <div className="agent-detail-metrics"><div><span>关注内容</span><strong>{meta.focus}</strong></div><div><span>当前工具</span><strong>{agent.tool || "尚未调用"}</strong></div><div><span>任务状态</span><strong>{statusText(displayStatus)}</strong></div></div>
    <DetailBlock label="最近输出" value={agent.output || (agent.status === "completed" ? "该 Agent 已完成，运行摘要未单独返回。" : "暂无输出") } markdown={Boolean(agent.output)} />
    <section className="detail-block"><h4>执行轨迹 <small>{agentEvents.length} 条事件</small></h4>{agentEvents.length === 0 ? <p className="detail-muted">该历史运行没有保存可展开的单 Agent 事件。</p> : <div className="agent-event-list">{agentEvents.slice(-20).reverse().map((event) => <article className="agent-event" key={event.id}><div className="agent-event-head"><div><strong>{eventDisplayName(event.type)}</strong><span>{time(event.timestamp)} · {event.label}</span></div><EventLayerPill layer={event.layer} /><StatusPill status={visibleEventStatus(event, workspaceStatus)} /></div><DetailBlock label="输入" value={event.input} /><DetailBlock label="输出" value={event.output} markdown={typeof event.output === "string"} /><details className="agent-event-raw"><summary>查看原始事件</summary><pre>{json(event.raw)}</pre></details></article>)}</div>}</section>
  </>;
}

function EventDetail({ event, workspaceStatus }: { event: WorkspaceEvent; workspaceStatus: WorkspaceSnapshot["status"] }): ReactElement {
  return <><div className="event-detail-hero"><EventLayerPill layer={event.layer} /><div><h3>{eventDisplayName(event.type)}</h3><span>{event.label} · {time(event.timestamp)}</span></div><StatusPill status={visibleEventStatus(event, workspaceStatus)} /></div><div className="event-detail-context"><span>Agent</span><strong>{event.agentId || "系统事件"}</strong><span>任务</span><strong>{event.taskId || "未标注"}</strong></div><DetailBlock label="输入" value={event.input} /><DetailBlock label="输出" value={event.output} markdown={typeof event.output === "string"} /><details className="raw-disclosure"><summary>查看完整原始事件 JSON</summary><pre>{json(event.raw)}</pre></details></>;
}

function DetailDrawer({ title, data, events, workspaceStatus, onClose }: { title: string; data: AgentSnapshot | WorkspaceEvent; events: WorkspaceEvent[]; workspaceStatus: WorkspaceSnapshot["status"]; onClose: () => void }): ReactElement {
  const isAgent = "role" in data;
  return <aside className="detail-drawer"><div className="drawer-head"><div><span className="eyebrow">{isAgent ? "AGENT DETAIL" : "EVENT DETAIL"}</span><h3>{isAgent ? agentUi(data).title : title}</h3></div><button className="icon-button" onClick={onClose} title="关闭详情"><XCircle size={18} /></button></div><div className="detail-content">{isAgent ? <AgentDetail agent={data} events={events} workspaceStatus={workspaceStatus} /> : <EventDetail event={data} workspaceStatus={workspaceStatus} />}</div></aside>;
}

function runTime(value?: string | null): string {
  if (!value) return "刚刚";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "刚刚" : date.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function runTitle(run: DebateRunSummary, index: number): string {
  const promptPair = run.prompt?.match(/\b[A-Z]{3}(?:[\/-]?[A-Z]{3})\b/i)?.[0];
  const target = typeof run.variables?.target === "string" ? run.variables.target : promptPair || "FX Debate";
  return `${target} · 第 ${index + 1} 次`;
}

function RunSwitcher({ summaries, activeRunId, onSelect }: { summaries: DebateRunSummary[]; activeRunId?: string; onSelect: (runId: string) => void }): ReactElement | null {
  if (!summaries.length) return null;
  const active = summaries.find((item) => item.run_id === activeRunId) || summaries[summaries.length - 1];
  return <section className="run-switcher" aria-label="Debate 运行选择">
    <div className="run-switcher-copy"><span className="eyebrow">DEBATE RUNS · {summaries.length}</span><strong>{active ? runTitle(active, summaries.indexOf(active)) : "选择一次运行"}</strong><small>{active?.prompt || "每次发送问题都会生成独立的协作画布和报告。"}</small></div>
    <div className="run-switcher-control"><label htmlFor="run-select">当前运行</label><select id="run-select" value={active?.run_id || ""} onChange={(event) => onSelect(event.target.value)}>{summaries.map((run, index) => <option key={run.run_id} value={run.run_id}>{runTitle(run, index)} · {statusText(run.status)} · {runTime(run.created_at)}</option>)}</select><StatusPill status={active?.status || "unknown"} /></div>
  </section>;
}

export default function App(): ReactElement {
  const transport = useRef(new SessionTransport()).current;
  const activeAttemptId = useRef<string | null>(null);
  const cancellationRequested = useRef(false);
  const [sessionId, setSessionId] = useState(() => new URLSearchParams(window.location.search).get("session") || "");
  const [historyEpoch, setHistoryEpoch] = useState(readHistoryEpoch);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [activeView, setActiveView] = useState<WorkspaceView>(readView);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [runWorkspace, setRunWorkspace] = useState(() => emptyRunWorkspace());
  const [draft, setDraft] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [reasoning, setReasoning] = useState(false);
  const [sseStatus, setSseStatus] = useState<SSEStatus>("disconnected");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(() => (localStorage.getItem("fx-debate-theme") as "light" | "dark") || "light");
  const [selected, setSelected] = useState<AgentSnapshot | WorkspaceEvent | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("fx-debate-theme", theme);
  }, [theme]);

  const refreshSessions = useCallback(() => {
    void api.listSessions(50).then((items) => setSessions(items.filter((item) => visibleSession(item, historyEpoch)))).catch(() => undefined);
  }, [historyEpoch]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const consume = useCallback((event: SessionEvent) => {
    const eventAttemptId = typeof event.data.attempt_id === "string" ? event.data.attempt_id : undefined;
    if (event.type === "text_delta") {
      setStreamingText((value) => value + String(event.data.delta || ""));
      setReasoning(false);
    } else if (event.type === "reasoning_delta") {
      setReasoning(true);
    } else if (event.type === "stream_reset") {
      setStreamingText("");
    } else if (event.type === "attempt.created") {
      if (eventAttemptId) activeAttemptId.current = eventAttemptId;
      if (!cancellationRequested.current) setCancelling(false);
    } else if (event.type === "attempt.started") {
      if (eventAttemptId) activeAttemptId.current = eventAttemptId;
      if (cancellationRequested.current) {
        setBusy(false);
        setReasoning(false);
      } else {
        setBusy(true);
        setCancelling(false);
        setReasoning(true);
      }
    } else if (event.type === "attempt.completed" || event.type === "attempt.failed") {
      const isCurrentAttempt = !eventAttemptId || !activeAttemptId.current || eventAttemptId === activeAttemptId.current;
      if (isCurrentAttempt) {
        const wasCancelled = cancellationRequested.current;
        setBusy(false);
        setCancelling(false);
        setReasoning(false);
        if (event.type === "attempt.failed" && !wasCancelled) setError(friendlyError(String(event.data.error || "Agent 执行失败")));
        if (event.type === "attempt.completed") setStreamingText("");
        cancellationRequested.current = false;
        void api.getMessages(sessionId).then(setMessages).catch(() => undefined);
        void api.listSessionRuns(sessionId).then((items) => setRunWorkspace((current) => replaceRunSummaries(current, items))).catch(() => undefined);
        refreshSessions();
      }
    }
    const runId = runIdFromEvent(event);
    setRunWorkspace((current) => applyRunEvent(current, event));
    if (runId) updateUrl({ run: runId });
  }, [refreshSessions, sessionId]);

  useEffect(() => {
    if (!sessionId) return undefined;
    let active = true;
    setError("");
    setRunWorkspace(emptyRunWorkspace(sessionId));
    void api.getSession(sessionId).then(() => {
      if (!active) return;
      void api.getMessages(sessionId).then((items) => {
        if (!active) return;
        setMessages(items);
      }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "无法加载 Session"); });
      void api.listSessionRuns(sessionId).then((items) => {
        if (!active) return;
        setRunWorkspace((current) => replaceRunSummaries({ ...current, sessionId }, items));
        const requested = new URLSearchParams(window.location.search).get("run");
        const latestRun = items.length ? items[items.length - 1] : undefined;
        const selectedRunId = (requested && items.some((item) => item.run_id === requested) ? requested : latestRun?.run_id);
        if (selectedRunId) {
          updateUrl({ run: selectedRunId });
          void api.getSwarmRun(selectedRunId).then((run) => { if (active) setRunWorkspace((current) => hydrateRunSnapshot(current, run as unknown as Record<string, unknown>)); }).catch(() => undefined);
        }
      }).catch(() => undefined);
      void api.sessionEventsUrl(sessionId).then((url) => { if (active) transport.connect(url, consume, setSseStatus); }).catch(() => { if (active) setSseStatus("disconnected"); });
    }).catch(() => {
      if (!active) return;
      // A deep-linked session from before the current history epoch may no longer
      // be readable after schema changes. Treat it as stale and start cleanly.
      setSessionId("");
      setMessages([]);
      setRunWorkspace(emptyRunWorkspace());
      activeAttemptId.current = null;
      cancellationRequested.current = false;
      setBusy(false);
      setCancelling(false);
      updateUrl({ session: undefined, run: undefined, view: "chat" });
    });
    return () => { active = false; transport.disconnect(); };
  }, [consume, historyEpoch, sessionId, transport]);

  const setView = (view: WorkspaceView) => {
    setActiveView(view);
    updateUrl({ view });
  };

  const startNewConversation = useCallback(() => {
    transport.disconnect();
    setSessionId("");
    setMessages([]);
    setRunWorkspace(emptyRunWorkspace());
    setDraft("");
    setStreamingText("");
    setReasoning(false);
    setBusy(false);
    setCancelling(false);
    activeAttemptId.current = null;
    cancellationRequested.current = false;
    setError("");
    setActiveView("chat");
    updateUrl({ session: undefined, run: undefined, view: "chat" });
  }, [transport]);

  const resetVisibleHistory = useCallback(() => {
    const now = Date.now();
    localStorage.setItem(HISTORY_EPOCH_KEY, String(now));
    setHistoryEpoch(now);
    startNewConversation();
  }, [startNewConversation]);

  const openSession = useCallback((id: string) => {
    setError("");
    setActiveView("chat");
    setSessionId(id);
    updateUrl({ session: id, run: undefined, view: "chat" });
  }, []);

  const selectRun = useCallback((runId: string) => {
    setRunWorkspace((current) => selectRunState(current, runId));
    updateUrl({ run: runId });
    if (!runWorkspace.snapshots[runId]) {
      void api.getSwarmRun(runId).then((run) => setRunWorkspace((current) => hydrateRunSnapshot(current, run as unknown as Record<string, unknown>))).catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "无法加载该运行"));
    }
    setSelected(null);
  }, [runWorkspace.snapshots]);

  const cancelRun = useCallback(async () => {
    if (!sessionId || cancelling) return;
    cancellationRequested.current = true;
    setCancelling(true);
    try {
      const response = await api.cancelSession(sessionId);
      const next = settleCancellation({ busy: true, cancelling: true }, response.status);
      setBusy(next.busy);
      setCancelling(next.cancelling);
      if (!next.busy) {
        setRunWorkspace((current) => markActiveRunCancelled(current));
        setReasoning(false);
        setStreamingText("");
      }
    } catch (cause: unknown) {
      cancellationRequested.current = false;
      setCancelling(false);
      setError(cause instanceof Error ? cause.message : "停止运行失败");
    }
  }, [cancelling, sessionId]);

  const workspace = activeSnapshot(runWorkspace);
  const runActive = isRunActive(busy, workspace.status);

  const send = async () => {
    const content = draft.trim();
    if (!content || runActive) return;
    setError("");
    setDraft("");
    setStreamingText("");
    setReasoning(false);
    setBusy(true);
    setCancelling(false);
    cancellationRequested.current = false;
    activeAttemptId.current = null;
    try {
      let currentSession = sessionId;
      if (!currentSession) {
        const created = await api.createSession("FX Debate");
        currentSession = created.session_id;
        setSessionId(currentSession);
        updateUrl({ session: currentSession, view: "chat" });
        setSessions((items) => [created, ...items.filter((item) => item.session_id !== created.session_id)]);
      }
      setMessages((items) => [...items, { message_id: `local-${Date.now()}`, session_id: currentSession, role: "user", content, created_at: new Date().toISOString() }]);
      const response = await api.sendMessage(currentSession, content);
      if (response.attempt_id) activeAttemptId.current = response.attempt_id;
      refreshSessions();
    } catch (cause: unknown) {
      setBusy(false);
      setError(cause instanceof Error ? cause.message : "发送失败");
    }
  };

  const quickPrompt = "分析 EURUSD 未来两周走势，结合 4H 和 1D 周期，给出平衡风险偏好的交易建议，并明确入场、止损、止盈和失效条件。";
  const connectionLabel = workspace.status === "completed" && sseStatus !== "connected"
    ? "历史快照"
    : sseStatus === "connected" ? "Session 已连接" : sseStatus === "reconnecting" ? "正在重连" : "未连接";

  return <div className="app-shell">
    <header className="topbar"><button className="icon-button sidebar-toggle" onClick={() => setSidebarOpen((open) => !open)} title={sidebarOpen ? "收起对话历史" : "展开对话历史"}>{sidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeft size={17} />}</button><div className="brand"><div className="brand-mark"><Network size={17} /></div><div><strong>FX Debate</strong><span>Vibe-compatible research workspace</span></div></div><div className="topbar-center"><span className={`connection-dot connection-${sseStatus}`} />{connectionLabel}{workspace.runId && <code>{workspace.runId.slice(0, 12)}</code>}</div><div className="topbar-actions"><span className="backend-status"><Server size={14} /> Session/SSE</span><button className="icon-button" title={theme === "light" ? "切换到深色" : "切换到浅色"} onClick={() => setTheme(theme === "light" ? "dark" : "light")}>{theme === "light" ? <Moon size={17} /> : <Sun size={17} />}</button></div></header>
    <nav className="tabs" aria-label="工作区视图">{(Object.keys(VIEW_LABELS) as WorkspaceView[]).map((view) => { const Icon = VIEW_ICONS[view]; return <button key={view} className={activeView === view ? "tab-active" : ""} onClick={() => setView(view)}><Icon size={16} />{VIEW_LABELS[view]}{view === "canvas" && workspace.agents.length > 0 ? <span className="tab-count">{workspace.agents.length}</span> : null}</button>; })}</nav>
    <div className={`workspace-body ${sidebarOpen ? "" : "sidebar-collapsed"}`}>
      <SessionSidebar sessions={sessions} activeSessionId={sessionId} onNew={startNewConversation} onSelect={openSession} onReset={resetVisibleHistory} />
      <main className="main-content">
      {error && <div className="error-banner"><AlertCircle size={16} />{error}<button onClick={() => setError("")} title="关闭"><XCircle size={15} /></button></div>}
      <RunSwitcher summaries={runWorkspace.summaries} activeRunId={runWorkspace.activeRunId} onSelect={selectRun} />
      {activeView === "chat" && <ChatView messages={messages} streamingText={streamingText} reasoning={reasoning} workspace={workspace} onView={setView} onSelectAgent={setSelected} />}
      {activeView === "canvas" && <CanvasView workspace={workspace} onSelect={setSelected} selectedAgentId={selected && "role" in selected ? selected.id : undefined} />}
      {activeView === "data" && <DataView workspace={workspace} />}
      {activeView === "logs" && <LogsView events={workspace.events} runStatus={workspace.status} onSelect={setSelected} />}
      {activeView === "report" && <ReportView report={workspace.report} workspace={workspace} />}
      {activeView === "settings" && <SettingsView />}
      </main>
    </div>
    {activeView === "chat" && <footer className="composer-wrap"><div className="composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="描述你想研究的外汇问题…" rows={2} /><div className="composer-bottom"><button className="prompt-button" onClick={() => setDraft(quickPrompt)} title="填入 EURUSD 示例问题">EURUSD 示例</button><span>Enter 发送 · Shift+Enter 换行</span><button className="send-button" disabled={runActive || !draft.trim()} onClick={() => void send()} title={runActive ? "运行中" : "发送"}>{runActive ? <Square size={16} /> : <Send size={16} />}</button></div></div>{runActive && <button className="cancel-button" disabled={cancelling} onClick={() => void cancelRun()}><Square size={14} />{cancelling ? "正在停止…" : "停止运行"}</button>}</footer>}
    {selected && <DetailDrawer title={"role" in selected ? selected.role : selected.label} data={selected} events={workspace.events} workspaceStatus={workspace.status} onClose={() => setSelected(null)} />}
    {workspace.status === "completed" && activeView === "chat" && workspace.report && <button className="floating-report" onClick={() => setView("report")}><FileText size={15} />查看最终报告</button>}
  </div>;
}
