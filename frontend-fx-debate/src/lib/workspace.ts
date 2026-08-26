import type {
  AgentReport,
  AgentSnapshot,
  EvidenceBundle,
  EvidenceItem,
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

export const FX_AGENT_ORDER = ["pair_bull", "pair_bear", "pair_bull_debate", "pair_bear_debate", "macro_technical", "fx_risk_officer", "debate_judge"];

const FX_REPORT_AGENT_ORDER = ["pair_bull", "pair_bear", "macro_technical", "fx_risk_officer"];
const FX_REPORT_ROLE_LABELS: Record<string, string> = {
  pair_bull: "多头观点分析师",
  pair_bear: "空头观点分析师",
  macro_technical: "宏观与技术分析师",
  fx_risk_officer: "外汇风险分析师",
};

export const FX_AGENT_DEFINITIONS: AgentSnapshot[] = [
  { id: "pair_bull", role: "Pair Bull", status: "pending" },
  { id: "pair_bear", role: "Pair Bear", status: "pending" },
  { id: "pair_bull_debate", role: "Pair Bull Debate", status: "pending" },
  { id: "pair_bear_debate", role: "Pair Bear Debate", status: "pending" },
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

/**
 * A report payload can arrive before the terminal run event. It is still an
 * intermediate artifact until the workflow has completed successfully.
 */
export function isFinalReportReady(status: WorkspaceSnapshot["status"]): boolean {
  return status === "completed";
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
  if (effectiveType.startsWith("data_service.")) {
    const transport = String(data.transport || data.source || "").toLowerCase();
    return transport === "mcp_stdio" || transport === "mcp" ? "MCP" : "SDK";
  }
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

function deriveAgentReports(tasks: SwarmTask[], agents: SwarmAgent[]): AgentReport[] {
  const byAgent = new Map(agents.map((agent) => [normalizeFxAgentId(agent.id) || agent.id, agent]));
  const byId = new Map(tasks.map((task) => [normalizeFxAgentId(task.agent_id) || task.agent_id, task]));
  return FX_REPORT_AGENT_ORDER.flatMap((agentId) => {
    const task = byId.get(agentId);
    const report = task?.summary?.trim();
    if (!task || !report) return [];
    const agent = byAgent.get(agentId);
    return [{
      taskId: task.id,
      agentId,
      role: FX_REPORT_ROLE_LABELS[agentId] || agent?.role || task.agent_id,
      status: task.status || "completed",
      report,
    } satisfies AgentReport];
  });
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
  if (typeof data === "string") {
    const markdown = data;
    let parsed: unknown;
    try {
      parsed = JSON.parse(markdown);
    } catch {
      const fenced = markdown.match(/```(?:json)?\s*([\s\S]*?)```/i)?.[1];
      if (fenced) {
        try { parsed = JSON.parse(fenced); } catch { parsed = undefined; }
      }
    }
    if (parsed && typeof parsed === "object") {
      const structured = extractReport(parsed);
      if (structured) return { ...structured, markdown, raw: data };
    }
    const inferredDecision = inferDecisionFromMarkdown(markdown);
    const inferredTradePlan = inferTradePlanFromMarkdown(markdown);
    return {
      ...(inferredDecision ? { direction: inferredDecision.direction, action: inferredDecision.action } : {}),
      ...inferredTradePlan,
      markdown,
      raw: data,
    };
  }
  if (typeof data !== "object") return undefined;
  const value = data as Record<string, unknown>;
  const embedded = value.final_report || value.report;
  if (embedded && typeof embedded === "object") {
    const structured = extractReport(embedded);
    if (structured) return structured;
  }
  if (typeof embedded === "string") {
    const structured = extractReport(embedded);
    if (structured) return structured;
  }
  const reportKeys = [
    "direction", "bias", "recommendation", "action", "decision", "confidence", "entry", "entry_range",
    "stop_loss", "take_profit", "trade_plan", "probabilities", "scenario_probabilities", "rationale",
    "thesis", "risk_assessment", "invalidation", "invalidation_conditions", "presentation", "markdown",
  ];
  if (!reportKeys.some((key) => key in value)) return undefined;
  const report = value;
  const tradePlan = report.trade_plan && typeof report.trade_plan === "object" ? report.trade_plan as Record<string, unknown> : {};
  const decision = displayDecision(asDisplayString(report.decision));
  const action = asDisplayString(report.action) || asDisplayString(report.trade_action) || (decision ? decision.action : undefined);
  const direction = asDisplayString(report.direction) || asDisplayString(report.bias) || asDisplayString(report.recommendation) || (decision ? decision.direction : undefined);
  const probabilities = (report.probabilities || report.scenario_probabilities) && typeof (report.probabilities || report.scenario_probabilities) === "object"
    ? (report.probabilities || report.scenario_probabilities) as Record<string, unknown>
    : undefined;
  const invalidationValue = report.invalidation || report.invalidation_conditions;
  const invalidation = arrayValue(invalidationValue);
  const presentationValue = report.presentation && typeof report.presentation === "object"
    ? report.presentation as Record<string, unknown>
    : undefined;
  const presentation = presentationValue
    ? {
      marketBackground: asDisplayString(presentationValue.market_background) || "宏观背景无法确定",
      backgroundStrength: asDisplayString(presentationValue.background_strength) || "low",
      technicalConfirmation: asDisplayString(presentationValue.technical_confirmation) || "无法确认",
      dataQuality: asDisplayString(presentationValue.data_quality) || "degraded",
      summary: asDisplayString(presentationValue.summary) || "当前证据不足以形成交易信号",
      usableEvidence: arrayValue(presentationValue.usable_evidence) || [],
      limitations: arrayValue(presentationValue.limitations) || [],
    }
    : undefined;
  return {
    direction,
    action,
    confidence: report.confidence as string | number | undefined,
    entry: asDisplayString(report.entry) || asDisplayString(report.entry_range) || asDisplayString(tradePlan.entry_zone),
    stopLoss: asDisplayString(report.stop_loss) || asDisplayString(report.stopLoss) || asDisplayString(tradePlan.stop_loss),
    takeProfit: asDisplayString(report.take_profit) || asDisplayString(report.takeProfit) || arrayValue(tradePlan.targets)?.join(" / "),
    holdingPeriod: asDisplayString(report.holding_period) || asDisplayString(report.holdingPeriod) || (asDisplayString(report.horizon_days) ? `${asDisplayString(report.horizon_days)} 天` : undefined),
    probabilities: probabilities ? {
      bullish: Number(probabilities.bullish ?? probabilities.bull ?? 0) || undefined,
      bearish: Number(probabilities.bearish ?? probabilities.bear ?? 0) || undefined,
      neutral: Number(probabilities.neutral ?? probabilities.base ?? probabilities.sideways ?? 0) || undefined,
    } : undefined,
    rationale: arrayValue(report.rationale) || (asDisplayString(report.thesis) ? [asDisplayString(report.thesis)!] : undefined),
    invalidation,
    risks: arrayValue(report.risks) || (asDisplayString(report.risk_assessment) ? [asDisplayString(report.risk_assessment)!] : undefined),
    presentation,
    markdown: asString(value.markdown),
    raw: value,
  };
}

function asDisplayString(value: unknown): string | undefined {
  const text = asString(value)?.trim();
  if (!text || ["null", "none", "n/a", "na", "undefined"].includes(text.toLowerCase())) return undefined;
  return text;
}

function arrayValue(value: unknown): string[] | undefined {
  if (Array.isArray(value)) return value.map(String).filter((item) => item.trim());
  const record = asRecord(value);
  if (record && Array.isArray(record.item)) return arrayValue(record.item);
  return asDisplayString(value) ? [asDisplayString(value)!] : undefined;
}

function displayDecision(value?: string): { direction: string; action: string } | undefined {
  if (!value) return undefined;
  const normalized = value.toLowerCase();
  if (["wait", "hold", "no_trade", "no-trade", "flat"].includes(normalized)) return { direction: "等待确认", action: "暂不交易" };
  if (["long", "buy", "bull", "up", "做多", "看涨", "偏多"].includes(normalized)) return { direction: "偏多", action: "做多" };
  if (["short", "sell", "bear", "down", "做空", "看跌", "偏空"].includes(normalized)) return { direction: "偏空", action: "做空" };
  return { direction: value, action: value };
}

function inferDecisionFromMarkdown(markdown: string): { direction: string; action: string } | undefined {
  const match = markdown.match(
    /(?:当前回测方向|决策)\s*[：:]\s*(做多|看涨|long|做空|看跌|short)(?:\s*[（(]\s*(long|short)\s*[）)])?/im,
  );
  return displayDecision(match?.[2] || match?.[1]);
}

function inferTradePlanFromMarkdown(markdown: string): Pick<FxReport, "entry" | "stopLoss" | "takeProfit"> {
  const match = markdown.match(
    /入场\s*([0-9]+(?:\.[0-9]+)?)\s*[–—-]\s*([0-9]+(?:\.[0-9]+)?)\s*[，,;；]\s*止损\s*([0-9]+(?:\.[0-9]+)?)\s*[，,;；]\s*(?:目标|止盈)\s*([^。\n]+)/i,
  );
  if (!match || [match[1], match[2], match[3]].some((value) => !value || value === "未生成")) {
    return {};
  }
  const targets = match[4]
    .replace(/[。；;，,]+$/g, "")
    .trim();
  if (!targets || targets === "未生成") return {};
  return {
    entry: `${match[1]}–${match[2]}`,
    stopLoss: match[3],
    takeProfit: targets,
  };
}

const EVIDENCE_DOMAINS = ["market", "technical", "macro", "news", "mcp"] as const;

const MCP_EVIDENCE_LABELS: Record<string, string> = {
  dataset_catalog: "数据集目录",
  dataset_query_context: "数据集查询上下文",
  dataset_search: "数据集检索",
  database_query: "数据库查询",
  tool_execution: "MCP 工具执行",
};

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function previewSummary(row: Record<string, unknown>): string | undefined {
  for (const key of ["summary", "description", "headline", "title", "value", "close", "price", "reading"]) {
    const value = row[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
  }
  const compact = Object.entries(row)
    .filter(([key]) => !["evidence_id", "id", "raw", "source", "as_of", "timestamp"].includes(key))
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(" · ");
  return compact || undefined;
}

function previewEvidence(
  event: WorkspaceEvent,
  data: Record<string, unknown>,
): EvidenceBundle | undefined {
  const preview = asRecord(data.data_preview) || asRecord(event.output);
  if (!preview) return undefined;
  const domains = asRecord(preview.domains);
  if (!domains) return undefined;

  const source = asString(preview.source) || asString(data.source);
  const asOf = asString(preview.as_of) || asString(data.as_of);
  const items: EvidenceItem[] = [];
  for (const domain of EVIDENCE_DOMAINS) {
    const group = asRecord(domains[domain]);
    if (!group) continue;
    const rows = Array.isArray(group.rows) ? group.rows : [];
    rows.forEach((value, index) => {
      const row = asRecord(value);
      if (!row) return;
      const id = asString(row.evidence_id) || asString(row.id) || `${event.id}-${domain}-${index}`;
      items.push({
        id,
        category: domain,
        title: asString(row.name) || asString(row.title) || asString(row.type) || `${domain} evidence ${index + 1}`,
        source: asString(row.source) || source,
        asOf: asString(row.as_of) || asString(row.timestamp) || asOf,
        summary: previewSummary(row),
        raw: row,
        status: asString(row.quality_status) || event.status,
      });
    });
    if (!rows.length && Number(group.count || 0) > 0) {
      items.push({
        id: `${event.id}-${domain}-summary`,
        category: domain,
        title: `${domain} evidence`,
        source,
        asOf,
        summary: `${String(group.count)} 条数据已返回，可在原始事件中查看完整结构。`,
        raw: group,
        status: event.status,
      });
    }
  }
  return {
    symbol: asString(preview.symbol) || asString(data.symbol),
    timeframe: asString(preview.timeframe) || asString(data.timeframe),
    asOf,
    source,
    items,
    raw: preview,
  };
}

function persistedEvidenceBundle(value: unknown): EvidenceBundle | undefined {
  const bundle = asRecord(value);
  if (!bundle) return undefined;
  const source = asString(bundle.source_name) || asString(bundle.source);
  const technical = asRecord(bundle.technical_regime);
  const timeframes = asRecord(technical?.timeframes);
  const timeframe = timeframes ? Object.keys(timeframes).join("/") : asString(bundle.timeframe);
  const rows = Array.isArray(bundle.evidence) ? bundle.evidence : [];
  const items: EvidenceItem[] = rows.flatMap((value, index) => {
    const row = asRecord(value);
    if (!row) return [];
    const rawCategory = asString(row.domain) || asString(row.category);
    const category = EVIDENCE_DOMAINS.includes(rawCategory as (typeof EVIDENCE_DOMAINS)[number])
      ? rawCategory as EvidenceItem["category"]
      : "other";
    return [{
      id: asString(row.evidence_id) || asString(row.id) || `persisted-evidence-${index}`,
      category,
      title: asString(row.name) || asString(row.title) || asString(row.type) || `${category} evidence ${index + 1}`,
      source: asString(row.source) || source,
      asOf: asString(row.observation_time) || asString(row.available_time) || asString(row.as_of) || asString(bundle.as_of),
      summary: previewSummary(row) || asString(row.notes),
      query: asString(row.source_identifier),
      raw: row,
      status: asString(row.quality_status),
    } satisfies EvidenceItem];
  });
  return {
    symbol: asString(bundle.symbol),
    timeframe,
    asOf: asString(bundle.as_of),
    source,
    items,
    raw: bundle,
  };
}

function updateEvidence(snapshot: WorkspaceSnapshot, event: WorkspaceEvent): EvidenceBundle {
  if (event.layer !== "SDK" && event.layer !== "MCP" && event.layer !== "DATABASE") return snapshot.evidence;
  const raw = event.raw as Record<string, unknown>;
  const nested = raw.event && typeof raw.event === "object" ? raw.event as Record<string, unknown> : raw;
  const data = nested.data && typeof nested.data === "object" ? nested.data as Record<string, unknown> : nested;
  const category: EvidenceItem["category"] = event.layer === "DATABASE"
    ? "database"
    : event.layer === "MCP" ? "mcp" : "sdk";
  const contextPreview = previewEvidence(event, data);
  if (contextPreview) {
    return {
      ...snapshot.evidence,
      ...contextPreview,
      items: [...snapshot.evidence.items, ...contextPreview.items],
    };
  }
  if (!event.output && !data.summary && !data.result && !data.preview) return snapshot.evidence;
  const outputRecord = asRecord(event.output);
  const outputValue = asRecord(outputRecord?.output) || outputRecord;
  const item: EvidenceBundle["items"][number] = {
    id: event.id,
    category,
    title: event.layer === "MCP"
      ? MCP_EVIDENCE_LABELS[event.stage || event.label] || `MCP 服务：${event.label}`
      : event.label,
    source: asString(data.source) || asString(data.provider) || (event.layer === "MCP" ? "mcp" : undefined),
    asOf: asString(data.as_of) || asString(data.timestamp),
    summary: asString(data.summary) || asString(data.preview) || asString(event.output)
      || (outputValue ? previewSummary(outputValue) : undefined),
    query: event.input,
    raw,
    status: event.status,
  };
  return {
    ...snapshot.evidence,
    source: snapshot.evidence.source || asString(data.source) || asString(data.provider),
    asOf: snapshot.evidence.asOf || asString(data.as_of),
    items: [...snapshot.evidence.items, item],
  };
}

export function applySessionEvent(snapshot: WorkspaceSnapshot, sessionEvent: SessionEvent): WorkspaceSnapshot {
  if (sessionEvent.type === "swarm.started") {
    const started = fromStarted(snapshot.sessionId, sessionEvent.data);
    return {
      ...started,
      // 历史流程日志需要完整保留，不能因为开始事件到达时重新组装快照
      // 就丢弃已经实时收到的 MCP、工具和数据库事件。
      events: [...snapshot.events, ...started.events],
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
    // 事件是流程日志的事实记录。这里不再设置数量上限，保证实时事件和
    // 后端历史回放事件在页面上使用同一份完整记录。
    events: [...snapshot.events, event],
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
  const agentReports = deriveAgentReports(tasks, swarmAgents);
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
  const persistedEvents = Array.isArray(run.events) ? run.events : [];
  let replayed = started;
  persistedEvents.forEach((raw, index) => {
    if (!raw || typeof raw !== "object") return;
    replayed = applySessionEvent(replayed, {
      id: `swarm-${String(run.id || "run")}-${index}`,
      type: "swarm.event",
      data: { run_id: run.id, event: raw },
    });
  });
  const persistedEvidence = persistedEvidenceBundle(run.evidence_bundle);
  const evidence = persistedEvidence
    ? {
      ...persistedEvidence,
      items: [
        ...persistedEvidence.items,
        ...replayed.evidence.items.filter((item) => !persistedEvidence.items.some((stored) => stored.id === item.id)),
      ],
    }
    : replayed.evidence;
  return {
    ...replayed,
    status: (String(run.status || "pending") as WorkspaceSnapshot["status"]),
    // 后端返回的 events.jsonl 是完整审计日志。历史页面应保留全部事件，
    // 这样 MCP 阶段不会因为后续 Agent 文本事件较多而从日志中消失。
    events: [...replayed.events, ...taskEvents],
    evidence,
    report: report ? { ...report, markdown: typeof run.final_report === "string" ? run.final_report : report.markdown } : fallbackReport,
    agentReports,
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
