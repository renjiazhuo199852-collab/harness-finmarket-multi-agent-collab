import type {
  DebateRunSummary,
  MessageItem,
  SessionItem,
  SwarmPresetDetail,
  SwarmPresetSummary,
  SwarmRunMeta,
  SwarmTask,
  AgentEditHistoryEntry,
  AgentEditProposal,
  AgentEditorPayload,
} from "@/types";
import { normalizeBaseUrl, readApiConfig, type ApiConfig } from "@/lib/api_config";

export const API_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

function configuredApiBase(): string {
  return normalizeBaseUrl(readApiConfig().backendUrl) || API_BASE;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const config = readApiConfig();
  const headers = new Headers(options?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (config.authToken && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${config.authToken}`);
  const response = await fetch(`${configuredApiBase()}${path}`, {
    ...options,
    headers,
  });
  const text = await response.text();
  if (!response.ok) {
    let detail = text;
    try {
      const body = JSON.parse(text) as { detail?: string; message?: string };
      detail = body.detail || body.message || detail;
    } catch {
      // Keep the text response when the server did not return JSON.
    }
    throw new ApiError(detail || `HTTP ${response.status}`, response.status);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

export interface SwarmRunDetail extends SwarmRunMeta {
  id: string;
  preset_name: string;
  user_vars?: Record<string, string>;
  final_report?: string | null;
  created_at?: string;
  completed_at?: string | null;
  events?: Array<Record<string, unknown>>;
  evidence_bundle?: Record<string, unknown> | null;
}

export interface ConnectionProbe {
  health: { status: string; service?: string };
  readiness: { ok: boolean; message?: string };
}

export interface BackendLiveProbe {
  status?: string;
  service?: string;
}

export interface ProviderProbe {
  ok: boolean;
  endpoint: string;
  status?: number;
  message?: string;
}

export interface LlmProviderOption {
  name: string;
  label: string;
  api_key_env?: string | null;
  base_url_env: string;
  default_model: string;
  default_base_url: string;
  base_url_options?: string[];
  api_key_required: boolean;
  auth_type?: string;
  login_command?: string | null;
}

export interface LlmSettings {
  provider: string;
  model_name: string;
  base_url: string;
  api_key_env?: string | null;
  api_key_configured: boolean;
  api_key_hint?: string | null;
  api_key_required: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort: string;
  sse_timeout_seconds: number;
  env_path: string;
  providers: LlmProviderOption[];
}

export interface UpdateLlmSettingsPayload {
  provider: string;
  model_name: string;
  base_url?: string;
  api_key?: string;
  clear_api_key?: boolean;
  temperature?: number;
  timeout_seconds?: number;
  max_retries?: number;
  reasoning_effort?: string;
}

export interface DataSourceSettings {
  tushare_token_configured: boolean;
  tushare_token_hint?: string | null;
  baostock_supported: boolean;
  baostock_installed: boolean;
  baostock_message: string;
  env_path: string;
}

export interface UpdateDataSourceSettingsPayload {
  tushare_token?: string;
  clear_tushare_token?: boolean;
}

export const api = {
  checkBackendLive: () => request<BackendLiveProbe>("/live"),
  createSession: (title = "FX Debate") =>
    request<SessionItem>("/sessions", { method: "POST", body: JSON.stringify({ title }) }),
  listSessions: (limit = 50) => request<SessionItem[]>(`/sessions?limit=${limit}`),
  getSession: (id: string) => request<SessionItem>(`/sessions/${encodeURIComponent(id)}`),
  deleteSession: (id: string) => request<{ status: string; session_id: string }>(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  listSessionRuns: (id: string, limit = 50) => request<DebateRunSummary[]>(`/sessions/${encodeURIComponent(id)}/runs?limit=${limit}`),
  sendMessage: (id: string, content: string) =>
    request<{ message_id: string; attempt_id?: string }>(`/sessions/${encodeURIComponent(id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  getMessages: (id: string) => request<MessageItem[]>(`/sessions/${encodeURIComponent(id)}/messages`),
  cancelSession: (id: string) => request<{ status: string }>(`/sessions/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  sessionEventsUrl: async (id: string) => {
    const query = new URLSearchParams({ replay: "active" });
    if (readApiConfig().authToken) {
      const ticket = await request<{ ticket: string }>("/auth/sse-ticket", { method: "POST" });
      if (ticket.ticket) query.set("ticket", ticket.ticket);
    }
    return `${configuredApiBase()}/sessions/${encodeURIComponent(id)}/events?${query.toString()}`;
  },
  testConnection: async (): Promise<ConnectionProbe> => {
    const health = await request<{ status: string; service?: string }>("/health");
    try {
      await request<{ status: string }>("/ready");
      return { health, readiness: { ok: true, message: "ready" } };
    } catch (error) {
      return { health, readiness: { ok: false, message: error instanceof Error ? error.message : "服务尚未就绪" } };
    }
  },
  testProvider: async (config: ApiConfig): Promise<ProviderProbe> => {
    return request<ProviderProbe>("/settings/llm/test", {
      method: "POST",
      body: JSON.stringify({
        provider: config.provider,
        base_url: normalizeBaseUrl(config.providerBaseUrl),
        api_key: config.providerApiKey.trim() || undefined,
      }),
    });
  },
  getSwarmRun: async (id: string) => {
    const data = await request<Record<string, unknown>>(`/swarm/runs/${encodeURIComponent(id)}`);
    return {
      id: String(data.id || id),
      runId: String(data.id || id),
      preset_name: String(data.preset_name || ""),
      preset: String(data.preset_name || ""),
      status: String(data.status || "pending") as SwarmRunMeta["status"],
      user_vars: (data.user_vars || {}) as Record<string, string>,
      variables: (data.user_vars || {}) as Record<string, string>,
      agents: Array.isArray(data.agents) ? (data.agents as SwarmRunMeta["agents"]) : [],
      tasks: Array.isArray(data.tasks) ? (data.tasks as SwarmTask[]) : [],
      final_report: typeof data.final_report === "string" ? data.final_report : null,
      events: Array.isArray(data.events) ? data.events as Array<Record<string, unknown>> : [],
      evidence_bundle: data.evidence_bundle && typeof data.evidence_bundle === "object" && !Array.isArray(data.evidence_bundle)
        ? data.evidence_bundle as Record<string, unknown>
        : null,
    } satisfies SwarmRunDetail;
  },
  createSwarmRun: (presetName: string, userVars: Record<string, string>) =>
    request<{ id: string; status: string; preset_name: string }>("/swarm/runs", {
      method: "POST",
      body: JSON.stringify({ preset_name: presetName, user_vars: userVars }),
    }),
  updateSwarmReport: (id: string, markdown: string) =>
    request<{ id: string; final_report: string; updated: boolean }>(`/swarm/runs/${encodeURIComponent(id)}/report`, {
      method: "PUT",
      body: JSON.stringify({ markdown }),
    }),
  listPresets: () => request<SwarmPresetSummary[]>("/swarm/presets"),
  getPreset: (name: string) => request<SwarmPresetDetail>(`/swarm/presets/${encodeURIComponent(name)}`),
  getAgentEditor: (preset: string, agent: string) => request<AgentEditorPayload>(`/swarm/presets/${encodeURIComponent(preset)}/agents/${encodeURIComponent(agent)}/editor`),
  proposeAgentEdit: (preset: string, agent: string, payload: { instruction: string; base_revision: string; session_id?: string }) =>
    request<AgentEditProposal>(`/swarm/presets/${encodeURIComponent(preset)}/agents/${encodeURIComponent(agent)}/proposals`, { method: "POST", body: JSON.stringify(payload) }),
  reviseAgentEdit: (preset: string, agent: string, proposalId: string, payload: { base_revision: string; candidate: AgentEditProposal["candidate"] }) =>
    request<AgentEditProposal>(`/swarm/presets/${encodeURIComponent(preset)}/agents/${encodeURIComponent(agent)}/proposals/${encodeURIComponent(proposalId)}/revise`, { method: "POST", body: JSON.stringify(payload) }),
  applyAgentEdit: (preset: string, agent: string, proposalId: string, baseRevision: string) =>
    request<AgentEditorPayload>(`/swarm/presets/${encodeURIComponent(preset)}/agents/${encodeURIComponent(agent)}/proposals/${encodeURIComponent(proposalId)}/apply`, { method: "POST", body: JSON.stringify({ base_revision: baseRevision }) }),
  resetAgentEdit: (preset: string, agent: string, baseRevision: string) =>
    request<AgentEditorPayload>(`/swarm/presets/${encodeURIComponent(preset)}/agents/${encodeURIComponent(agent)}/reset`, { method: "POST", body: JSON.stringify({ base_revision: baseRevision }) }),
  getAgentEditHistory: (preset: string, agent: string) => request<{ preset_name: string; agent_id: string; entries: AgentEditHistoryEntry[] }>(`/swarm/presets/${encodeURIComponent(preset)}/agents/${encodeURIComponent(agent)}/history`),
  reloadPreset: (preset: string) => request<{ preset_name: string; valid: boolean; errors: string[]; warnings: string[]; loaded_at: string; affects: string }>(`/swarm/presets/${encodeURIComponent(preset)}/reload`, { method: "POST", body: JSON.stringify({}) }),
  getLlmSettings: () => request<LlmSettings>("/settings/llm"),
  updateLlmSettings: (payload: UpdateLlmSettingsPayload) =>
    request<LlmSettings>("/settings/llm", { method: "PUT", body: JSON.stringify(payload) }),
  getDataSourceSettings: () => request<DataSourceSettings>("/settings/data-sources"),
  updateDataSourceSettings: (payload: UpdateDataSourceSettingsPayload) =>
    request<DataSourceSettings>("/settings/data-sources", { method: "PUT", body: JSON.stringify(payload) }),
};
