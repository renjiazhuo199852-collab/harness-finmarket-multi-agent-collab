import type {
  AgentSnapshot,
  EvidenceBundle,
  EventLayer,
  FxReport,
  MessageItem,
  SessionEvent,
  SwarmAgent,
  SwarmRunMeta,
  SwarmTask,
  WorkspaceEvent,
  WorkspaceSnapshot,
} from "@/types";

export const FX_AGENT_ORDER = ["pair_bull", "pair_bear", "macro_technical", "fx_risk_officer", "debate_judge"];

export const FX_AGENT_DEFINITIONS: AgentSnapshot[] = [
  { id: "pair_bull", role: "Pair Bull", status: "pending" },
  { id: "pair_bear", role: "Pair Bear", status: "pending" },
  { id: "macro_technical", role: "Macro + Technical", status: "pending" },
  { id: "fx_risk_officer", role: "FX Risk Officer", status: "pending" },
  { id: "debate_judge", role: "Debate Judge / FX PM", status: "pending" },
];

export function emptySnapshot(sessionId?: string): WorkspaceSnapshot {
  return {
    sessionId,
    status: "idle",
    variables: {},
    agents: [],
    tasks: [],
    events: [],
    evidence: { items: [] },
  };
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function normalizeFxAgentId(id?: string): string | undefined {
  if (!id) return undefined;
  return id === "risk_officer" ? "fx_risk_officer" : id;
}

function layerFor(type: string, data: Record<string, unknown>): EventLayer {
  const effectiveType = String(data.type || type);
  if (effectiveType === "context_ready" || type === "fx_debate.context_ready") return "SDK";
  if (type === "data_service.stage" || effectiveType === "data_service.stage" || effectiveType === "mcp.stage" || effectiveType === "mcp_stage") return "MCP";
  if (effectiveType.startsWith("tool_")) return "TOOL";
  if (effectiveType.startsWith("data_service.")) return "SDK";
  if (effectiveType.includes("database") || effectiveType.includes("db") || String(data.layer || "").toLowerCase() === "database") return "DATABASE";
  if (effectiveType.includes("sdk") || effectiveType.includes("reader")) return "SDK";
  if (effectiveType.startsWith("swarm.") || effectiveType.startsWith("attempt.") || effectiveType.startsWith("message.") || effectiveType === "heartbeat") return "SYSTEM";
  return "AGENT";
}

function eventLabel(type: string, data: Record<string, unknown>): string {
  const stage = asString(data.stage);
  if (stage && (type === "data_service.stage" || type === "mcp.stage" || data.type === "mcp_stage")) return stage;
  const tool = asString(data.tool);
  if (tool) return tool;
  if (stage) return stage;
  if (type === "swarm.event" && data.event && typeof data.event === "object") return String((data.event as Record<string, unknown>).type || type);
  return type.split(".").join(" ");
}

function now(): string {
  return new Date().toISOString();
}

function makeEvent(type: string, data: Record<string, unknown>, id: string, forcedLayer?: EventLayer): WorkspaceEvent {
  const nested = data.event && typeof data.event === "object" ? data.event as Record<string, unknown> : data;
  const eventData = nested.data && typeof nested.data === "object" ? nested.data as Record<string, unknown> : nested;
  const displayData = { ...nested, ...eventData };
  const eventType = String(nested.type || type);
  const rawStatus = asString(eventData.status) || asString(nested.status) || asString(data.status);
  const stage = asString(eventData.stage) || asString(nested.stage);
  return {
    id,
    type: eventType,
    layer: forcedLayer || layerFor(type, displayData),
    label: eventLabel(type, displayData),
    agentId: asString(nested.agent_id) || asString(data.agent_id),
    taskId: asString(nested.task_id) || asString(data.task_id),
    status: eventType === "worker_failed" || eventType === "task_retry" ? "retrying" : rawStatus,
    timestamp: asString(nested.timestamp) || now(),
    traceId: asString(eventData.trace_id) || asString(nested.trace_id),
    sequence: asNumber(eventData.sequence) ?? asNumber(nested.sequence),
    stage,
    durationMs: asNumber(eventData.duration_ms) ?? asNumber(nested.duration_ms),
    error: asString(eventData.error) || asString(nested.error),
    input: eventData.input || eventData.arguments || nested.input || nested.arguments,
    output: eventData.output || eventData.result || eventData.preview || eventData.result_preview
      || eventData.data_preview || (nested.type === "worker_text" ? eventData.content : undefined),
    raw: data,
  };
}

function appendAgentOutput(previous: string | undefined, chunk: string): string {
  const combined = `${previous || ""}${chunk}`;
  return combined.length > 12000 ? combined.slice(-12000) : combined;
}

function agentFrom(agent: SwarmAgent, task?: SwarmTask): AgentSnapshot {
  return {
    id: normalizeFxAgentId(agent.id) || agent.id,
    role: agent.role,
    taskId: task?.id,
    status: task?.status || "pending",
    output: task?.summary || undefined,
    error: task?.error || undefined,
    iterations: task?.worker_iterations ?? task?.iterations,
    startedAt: task?.started_at || undefined,
  };
}

function deriveAgents(agents: SwarmAgent[], tasks: SwarmTask[]): AgentSnapshot[] {
  const derived = agents.map((agent) => agentFrom(agent, tasks.find((task) => normalizeFxAgentId(task.agent_id) === normalizeFxAgentId(agent.id))));
  for (const task of tasks) {
    const id = normalizeFxAgentId(task.agent_id) || task.agent_id;
    if (!derived.some((agent) => agent.id === id)) {
      derived.push({
        id,
        role: task.agent_id,
        taskId: task.id,
        status: task.status || "pending",
        output: task.summary || undefined,
        error: task.error || undefined,
        startedAt: task.started_at || undefined,
      });
    }
  }
  return derived;
}

export function fromStarted(sessionId: string | undefined, data: Record<string, unknown>): WorkspaceSnapshot {
  const agents = Array.isArray(data.agents) ? data.agents as SwarmAgent[] : [];
  const tasks = Array.isArray(data.tasks) ? data.tasks as SwarmTask[] : [];
  const variables = data.variables && typeof data.variables === "object" ? data.variables as Record<string, string> : {};
  const agentSnapshots = deriveAgents(agents, tasks);
  return {
    sessionId,
    runId: asString(data.run_id),
    preset: asString(data.preset),
    status: (asString(data.status) as WorkspaceSnapshot["status"]) || "running",
    variables,
    agents: agentSnapshots,
    tasks,
    events: [makeEvent("swarm.started", data, `started-${String(data.run_id || Date.now())}`)],
    evidence: { items: [], raw: data },
  };
}

function updateTasks(tasks: SwarmTask[], event: WorkspaceEvent): SwarmTask[] {
  if (!event.taskId && !event.agentId) return tasks;
  const eventType = event.type;
  return tasks.map((task) => {
    const matchesTask = event.taskId ? task.id === event.taskId : normalizeFxAgentId(task.agent_id) === normalizeFxAgentId(event.agentId);
    if (!matchesTask) return task;
    let status = task.status;
    if (eventType.includes("started") || eventType === "worker_text" || eventType === "task_progress" || eventType === "task_heartbeat") status = "in_progress";
    if (eventType === "worker_failed" || eventType === "task_retry") status = "retrying";
    if (eventType.includes("completed")) status = "completed";
    if (eventType === "task_failed" || eventType === "run_failed" || eventType === "run_error") status = "failed";
    if (eventType.includes("blocked")) status = "blocked";
    if (eventType.includes("cancel")) status = "cancelled";
    return {
      ...task,
      status,
      summary: typeof event.output === "string" && eventType.includes("completed") ? event.output : task.summary,
      error: eventType === "task_failed" && typeof event.output === "string" ? event.output
        : ["worker_failed", "task_retry", "worker_started", "task_started"].includes(eventType) ? undefined : task.error,
    };
  });
}

function updateAgent(agents: AgentSnapshot[], event: WorkspaceEvent): AgentSnapshot[] {
  if (!event.agentId && !event.taskId) return agents;
  const eventAgentId = normalizeFxAgentId(event.agentId);
  return agents.map((agent) => {
    if (eventAgentId && normalizeFxAgentId(agent.id) !== eventAgentId && event.taskId !== agent.taskId) return agent;
    const type = event.type;
    const raw = event.raw as Record<string, unknown>;
    const nested = raw.event && typeof raw.event === "object" ? raw.event as Record<string, unknown> : raw;
    const data = nested.data && typeof nested.data === "object" ? nested.data as Record<string, unknown> : nested;
    let status = agent.status;
    if (type.includes("started") || type.includes("in_progress") || type === "worker_started" || type === "task_heartbeat" || type === "tool_call" || type === "worker_text" || type === "task_progress") status = "in_progress";
    if (type === "worker_failed" || type === "task_retry") status = "retrying";
    if (type.includes("completed")) status = "completed";
    if (type === "task_failed" || type === "run_failed" || type === "run_error") status = "failed";
    if (type.includes("blocked")) status = "blocked";
    if (type.includes("cancel")) status = "cancelled";
    return {
      ...agent,
      taskId: event.taskId || agent.taskId,
      status,
      tool: event.layer === "TOOL" ? event.label : asString(data.tool) || agent.tool,
      elapsedMs: Number(data.elapsed_ms || data.elapsedMs || 0)
        || (Number(data.elapsed_s || 0) ? Number(data.elapsed_s) * 1000 : agent.elapsedMs),
      iterations: Number(data.iterations || agent.iterations || 0) || undefined,
      output: type === "worker_text" && asString(data.content)
        ? appendAgentOutput(agent.output, asString(data.content)!)
        : asString(data.output) || asString(data.summary) || agent.output,
      error: type === "task_failed" || type === "run_failed" || type === "run_error" ? asString(data.error) || agent.error
        : ["worker_failed", "task_retry", "worker_started", "task_started"].includes(type) ? undefined : agent.error,
      startedAt: agent.startedAt || event.timestamp,
    };
  });
}

function extractReport(data: unknown): FxReport | undefined {
  if (!data) return undefined;
  const value = typeof data === "string" ? (() => { try { return JSON.parse(data) as unknown; } catch { return undefined; } })() : data;
  if (!value || typeof value !== "object") return undefined;
  const obj = value as Record<string, unknown>;
  const rawReport = obj.final_report || obj.report || obj.decision;
  if (typeof rawReport === "string") {
    const parsed = (() => { try { return JSON.parse(rawReport) as unknown; } catch { return undefined; } })();
    if (!parsed || typeof parsed !== "object") return { markdown: rawReport, raw: value };
    return extractReport(parsed) || { markdown: rawReport, raw: value };
  }
  const reportKeys = ["direction", "bias", "recommendation", "action", "confidence", "entry", "entry_range", "stop_loss", "take_profit", "probabilities", "rationale", "markdown"];
  if (!rawReport && !reportKeys.some((key) => key in obj)) return undefined;
  const report = (rawReport && typeof rawReport === "object" ? rawReport : obj) as Record<string, unknown>;
  if (!report || typeof report !== "object") return undefined;
  const probabilities = report.probabilities && typeof report.probabilities === "object" ? report.probabilities as Record<string, unknown> : undefined;
  return {
    direction: asString(report.direction) || asString(report.bias) || asString(report.recommendation),
    action: asString(report.action) || asString(report.trade_action),
    confidence: report.confidence as string | number | undefined,
    entry: asString(report.entry) || asString(report.entry_range),
    stopLoss: asString(report.stop_loss) || asString(report.stopLoss),
    takeProfit: asString(report.take_profit) || asString(report.takeProfit),
    holdingPeriod: asString(report.holding_period) || asString(report.holdingPeriod),
    probabilities: probabilities ? {
      bullish: Number(probabilities.bullish ?? probabilities.bull ?? 0) || undefined,
      bearish: Number(probabilities.bearish ?? probabilities.bear ?? 0) || undefined,
      neutral: Number(probabilities.neutral ?? probabilities.sideways ?? 0) || undefined,
    } : undefined,
    rationale: Array.isArray(report.rationale) ? report.rationale.map(String) : undefined,
    invalidation: Array.isArray(report.invalidation) ? report.invalidation.map(String) : undefined,
    risks: Array.isArray(report.risks) ? report.risks.map(String) : undefined,
    markdown: asString(obj.final_report) || asString(obj.markdown),
    raw: value,
  };
}

function updateEvidence(snapshot: WorkspaceSnapshot, event: WorkspaceEvent): EvidenceBundle {
  if (event.layer !== "SDK" && event.layer !== "DATABASE") return snapshot.evidence;
  const raw = event.raw as Record<string, unknown>;
  const nested = raw.event && typeof raw.event === "object" ? raw.event as Record<string, unknown> : raw;
  const data = nested.data && typeof nested.data === "object" ? nested.data as Record<string, unknown> : nested;
  const category = event.layer === "DATABASE" ? "database" : "sdk";
  if (!event.output && !data.summary && !data.result && !data.preview) return snapshot.evidence;
  const item: EvidenceBundle["items"][number] = {
    id: event.id,
    category,
    title: event.label,
    source: asString(data.source) || asString(data.provider),
    asOf: asString(data.as_of) || asString(data.timestamp),
    summary: asString(data.summary) || asString(data.preview) || asString(event.output),
    query: event.input,
    raw,
    status: event.status,
  };
  return { ...snapshot.evidence, items: [...snapshot.evidence.items, item] };
}

export function applySessionEvent(snapshot: WorkspaceSnapshot, sessionEvent: SessionEvent): WorkspaceSnapshot {
  if (sessionEvent.type === "swarm.started") {
    const started = fromStarted(snapshot.sessionId, sessionEvent.data);
    return {
      ...started,
      events: [...snapshot.events, ...started.events].slice(-500),
      evidence: snapshot.evidence.items.length ? snapshot.evidence : started.evidence,
    };
  }
  if (sessionEvent.type === "heartbeat" || sessionEvent.type === "message.received") return snapshot;
  const event = makeEvent(sessionEvent.type, sessionEvent.data, sessionEvent.id || `${sessionEvent.type}-${Date.now()}`);
  let status = snapshot.status;
  if (sessionEvent.type === "attempt.started") status = "running";
  if (sessionEvent.type === "attempt.completed") status = "completed";
  if (sessionEvent.type === "attempt.failed") status = "failed";
  if (sessionEvent.type === "swarm.event") {
    const nested = sessionEvent.data.event && typeof sessionEvent.data.event === "object" ? sessionEvent.data.event as Record<string, unknown> : {};
    const nestedType = String(nested.type || "");
    const nestedData = nested.data && typeof nested.data === "object" ? nested.data as Record<string, unknown> : nested;
    const terminalStatus = asString(nestedData.status);
    if (nestedType === "run_started") status = "running";
    if (nestedType === "run_completed") status = terminalStatus === "failed" || terminalStatus === "cancelled" ? terminalStatus : "completed";
    if (nestedType === "run_failed" || nestedType === "run_error") status = "failed";
  }
  let variables = snapshot.variables;
  if (event.type === "tool_call" && event.label === "run_fx_debate" && event.input && typeof event.input === "object") {
    const input = event.input as Record<string, unknown>;
    variables = {
      ...variables,
      ...(asString(input.target) ? { target: asString(input.target)! } : {}),
      ...(asString(input.timeframe) ? { timeframe: asString(input.timeframe)! } : {}),
    };
  }
  const next = {
    ...snapshot,
    status,
    variables,
    events: [...snapshot.events, event].slice(-500),
    agents: updateAgent(snapshot.agents, event),
    tasks: updateTasks(snapshot.tasks, event),
  };
  return {
    ...next,
    evidence: updateEvidence(next, event),
    report: extractReport(sessionEvent.data) || extractReport(event.output) || next.report,
    lastError: sessionEvent.type === "attempt.failed" ? asString(sessionEvent.data.error) : next.lastError,
  };
}

export function hydrateRun(sessionId: string | undefined, run: Record<string, unknown>): WorkspaceSnapshot {
  const tasks = Array.isArray(run.tasks) ? run.tasks as SwarmTask[] : [];
  const swarmAgents = Array.isArray(run.agents) ? run.agents as SwarmAgent[] : [];
  const started = fromStarted(sessionId, {
    run_id: run.id,
    preset: run.preset_name,
    status: run.status,
    variables: run.user_vars,
    agents: swarmAgents,
    tasks,
  });
  const report = extractReport(run.final_report);
  const fallbackReport = typeof run.final_report === "string" && run.final_report.trim()
    ? { markdown: run.final_report, raw: run.final_report }
    : undefined;
  const taskEvents: WorkspaceEvent[] = tasks.map((task) => {
    const agentId = normalizeFxAgentId(task.agent_id) || task.agent_id;
    const agent = swarmAgents.find((candidate) => normalizeFxAgentId(candidate.id) === agentId);
    const status = task.status || "pending";
    return {
      id: `task-${task.id}`,
      type: `task.${status}`,
      layer: "AGENT",
      label: agent?.role || agentId,
      agentId,
      taskId: task.id,
      status,
      timestamp: task.completed_at || task.started_at || now(),
      output: task.summary || task.error || undefined,
      raw: task,
    };
  });
  return {
    ...started,
    status: (String(run.status || "pending") as WorkspaceSnapshot["status"]),
    events: [...started.events, ...taskEvents],
    report: report ? { ...report, markdown: typeof run.final_report === "string" ? run.final_report : report.markdown } : fallbackReport,
  };
}

export function hydrateHistoricalMessage(sessionId: string | undefined, message: MessageItem): WorkspaceSnapshot {
  const metadata = message.metadata || {};
  const runId = asString(metadata.swarm_run_id) || asString(metadata.run_id);
  const report = extractReport(message.content) || { markdown: message.content, raw: message.content };
  const timestamp = message.created_at || now();
  return {
    sessionId,
    runId,
    preset: "fx_debate_team",
    status: "completed",
    variables: {},
    agents: [],
    tasks: [],
    events: [{
      id: `historical-${message.message_id}`,
      type: "historical.completed",
      layer: "SYSTEM",
      label: "历史运行已完成",
      status: "completed",
      timestamp,
      output: message.content,
      raw: message,
    }],
    evidence: { items: [] },
    report,
  };
}

export function isFxPreset(meta: SwarmRunMeta | WorkspaceSnapshot): boolean {
  const preset = String(meta.preset || "").toLowerCase();
  return preset.includes("fx") || preset.includes("debate") || meta.agents.some((agent) => FX_AGENT_ORDER.includes(agent.id));
}
