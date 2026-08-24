export type WorkspaceView = "chat" | "swarm" | "canvas" | "data" | "logs" | "report" | "settings";
export type RunStatus = "idle" | "pending" | "running" | "completed" | "failed" | "cancelled";
export type EventLayer = "AGENT" | "TOOL" | "MCP" | "SDK" | "DATABASE" | "SYSTEM";

export interface SessionItem {
  session_id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  last_attempt_id?: string | null;
  swarm_run_id?: string | null;
}

export interface DebateRunSummary {
  run_id: string;
  session_id: string;
  attempt_id?: string | null;
  prompt?: string;
  preset?: string | null;
  status: RunStatus | string;
  created_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  variables?: Record<string, unknown>;
}

export interface MessageItem {
  message_id: string;
  session_id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
  linked_attempt_id?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface SwarmAgent {
  id: string;
  role: string;
  system_prompt?: string;
  tools?: string[];
  skills?: string[];
}

export interface SwarmPresetSummary {
  name: string;
  title?: string;
  description?: string;
  agent_count?: number;
  variables?: Array<{ name?: string; description?: string; required?: boolean } | string>;
  source?: "bundled" | "user" | string;
}

export interface SwarmPresetAgent {
  id: string;
  role: string;
  tools: string[];
  skills: string[];
}

export interface SwarmPresetTask {
  id: string;
  agent_id: string;
  depends_on: string[];
  input_from: Record<string, string>;
}

export interface SwarmPresetLayerNode {
  task_id: string;
  agent_id: string;
}

export interface SwarmPresetDetail extends SwarmPresetSummary {
  valid?: boolean;
  errors?: string[];
  warnings?: string[];
  agents: SwarmPresetAgent[];
  tasks: SwarmPresetTask[];
  layers: SwarmPresetLayerNode[][];
  file?: string | null;
}

export interface SwarmTask {
  id: string;
  agent_id: string;
  status: string;
  depends_on?: string[];
  blocked_by?: string[];
  input_from?: Record<string, string>;
  summary?: string | null;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  worker_iterations?: number;
  iterations?: number;
}

export interface SwarmRunMeta {
  runId: string;
  preset?: string;
  status: RunStatus;
  variables?: Record<string, string>;
  agents: SwarmAgent[];
  tasks: SwarmTask[];
}

export interface AgentSnapshot {
  id: string;
  role: string;
  taskId?: string;
  status: string;
  tool?: string;
  elapsedMs?: number;
  iterations?: number;
  output?: string;
  error?: string;
  startedAt?: string;
}

export interface WorkspaceEvent {
  id: string;
  type: string;
  layer: EventLayer;
  label: string;
  agentId?: string;
  taskId?: string;
  status?: string;
  timestamp: string;
  traceId?: string;
  sequence?: number;
  stage?: string;
  durationMs?: number;
  error?: string;
  input?: unknown;
  output?: unknown;
  raw: unknown;
}

export interface EvidenceItem {
  id: string;
  category: "market" | "news" | "technical" | "macro" | "mcp" | "database" | "sdk" | "other";
  title: string;
  source?: string;
  asOf?: string;
  summary?: string;
  query?: unknown;
  raw?: unknown;
  status?: string;
}

export interface EvidenceBundle {
  symbol?: string;
  timeframe?: string;
  asOf?: string;
  source?: string;
  items: EvidenceItem[];
  raw?: unknown;
}

export interface FxPresentationSummary {
  marketBackground: string;
  backgroundStrength: string;
  technicalConfirmation: string;
  dataQuality: string;
  summary: string;
  usableEvidence: string[];
  limitations: string[];
}

export interface FxReport {
  direction?: string;
  probabilities?: { bullish?: number; bearish?: number; neutral?: number };
  confidence?: string | number;
  action?: string;
  entry?: string;
  stopLoss?: string;
  takeProfit?: string;
  holdingPeriod?: string;
  rationale?: string[];
  invalidation?: string[];
  risks?: string[];
  presentation?: FxPresentationSummary;
  markdown?: string;
  raw?: unknown;
}

export interface AgentReport {
  taskId: string;
  agentId: string;
  role: string;
  status: string;
  report: string;
}

export interface WorkspaceSnapshot {
  sessionId?: string;
  runId?: string;
  preset?: string;
  status: RunStatus;
  variables: Record<string, string>;
  agents: AgentSnapshot[];
  tasks: SwarmTask[];
  events: WorkspaceEvent[];
  evidence: EvidenceBundle;
  report?: FxReport;
  agentReports?: AgentReport[];
  lastError?: string;
}

export interface SessionEvent {
  id?: string;
  type: string;
  data: Record<string, unknown>;
}
