import type { DebateRunSummary, MessageItem, SessionEvent, WorkspaceSnapshot } from "@/types";
import { applySessionEvent, emptySnapshot, fromStarted, hydrateHistoricalMessage, hydrateRun } from "@/lib/workspace";

export interface RunWorkspaceState {
  sessionId?: string;
  activeRunId?: string;
  summaries: DebateRunSummary[];
  snapshots: Record<string, WorkspaceSnapshot>;
  pendingSnapshot: WorkspaceSnapshot;
}

export function emptyRunWorkspace(sessionId?: string): RunWorkspaceState {
  return { sessionId, summaries: [], snapshots: {}, pendingSnapshot: emptySnapshot(sessionId) };
}

export function runIdFromEvent(event: SessionEvent): string | undefined {
  const direct = event.data.run_id;
  if (typeof direct === "string" && direct) return direct;
  const nested = event.data.event;
  if (nested && typeof nested === "object") {
    const nestedRunId = (nested as Record<string, unknown>).run_id;
    if (typeof nestedRunId === "string" && nestedRunId) return nestedRunId;
    const nestedData = (nested as Record<string, unknown>).data;
    if (nestedData && typeof nestedData === "object") {
      const dataRunId = (nestedData as Record<string, unknown>).run_id;
      if (typeof dataRunId === "string" && dataRunId) return dataRunId;
    }
  }
  return undefined;
}

function statusOf(value: unknown): DebateRunSummary["status"] {
  return typeof value === "string" && value ? value : "running";
}

export function summaryFromStarted(sessionId: string | undefined, event: SessionEvent): DebateRunSummary | undefined {
  const runId = runIdFromEvent(event);
  if (!runId) return undefined;
  const data = event.data;
  return {
    run_id: runId,
    session_id: sessionId || "",
    preset: typeof data.preset === "string" ? data.preset : undefined,
    status: statusOf(data.status),
    created_at: new Date().toISOString(),
    variables: data.variables && typeof data.variables === "object" ? data.variables as Record<string, unknown> : {},
  };
}

export function summaryFromRun(sessionId: string | undefined, run: Record<string, unknown>): DebateRunSummary {
  return {
    run_id: String(run.id || run.run_id || ""),
    session_id: sessionId || "",
    preset: typeof run.preset_name === "string" ? run.preset_name : typeof run.preset === "string" ? run.preset : undefined,
    status: statusOf(run.status),
    created_at: typeof run.created_at === "string" ? run.created_at : undefined,
    completed_at: typeof run.completed_at === "string" ? run.completed_at : undefined,
    variables: run.user_vars && typeof run.user_vars === "object" ? run.user_vars as Record<string, unknown> : {},
  };
}

export function replaceRunSummaries(state: RunWorkspaceState, summaries: DebateRunSummary[]): RunWorkspaceState {
  const unique = new Map(summaries.filter((summary) => summary.run_id).map((summary) => [summary.run_id, summary]));
  const ordered = [...unique.values()].sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")));
  const activeRunId = state.activeRunId && unique.has(state.activeRunId) ? state.activeRunId : ordered[ordered.length - 1]?.run_id;
  return { ...state, activeRunId, summaries: ordered };
}

export function hydrateRunSnapshot(state: RunWorkspaceState, run: Record<string, unknown>): RunWorkspaceState {
  const snapshot = hydrateRun(state.sessionId, run);
  const summary = summaryFromRun(state.sessionId, run);
  const existing = snapshot.runId ? state.snapshots[snapshot.runId] : undefined;
  const mergedSnapshot = existing?.report && !snapshot.report ? { ...snapshot, report: existing.report } : snapshot;
  return {
    ...state,
    activeRunId: mergedSnapshot.runId || state.activeRunId,
    summaries: [...state.summaries.filter((item) => item.run_id !== summary.run_id), summary].sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || ""))),
    snapshots: mergedSnapshot.runId ? { ...state.snapshots, [mergedSnapshot.runId]: mergedSnapshot } : state.snapshots,
  };
}

/** Restore reports from persisted assistant messages for older/incomplete run records. */
export function hydrateReportsFromMessages(state: RunWorkspaceState, messages: MessageItem[]): RunWorkspaceState {
  if (!messages.length) return state;
  const assistantMessages = messages.filter((message) => message.role === "assistant");
  let changed = false;
  const snapshots: Record<string, WorkspaceSnapshot> = { ...state.snapshots };
  for (const summary of state.summaries) {
    const runId = summary.run_id;
    const message = assistantMessages.find((candidate) => {
      const metadata = candidate.metadata || {};
      return metadata.swarm_run_id === runId || metadata.run_id === runId;
    });
    if (!message) continue;
    const current = snapshots[runId];
    const historical = hydrateHistoricalMessage(state.sessionId, message);
    if (current) {
      if (current.report) continue;
      snapshots[runId] = { ...current, report: historical.report };
    } else {
      snapshots[runId] = {
        ...historical,
        runId,
        preset: summary.preset || historical.preset,
        status: (summary.status as WorkspaceSnapshot["status"]) || historical.status,
        variables: Object.fromEntries(Object.entries(summary.variables || {}).map(([key, value]) => [key, String(value)])),
      };
    }
    changed = true;
  }
  return changed ? { ...state, snapshots } : state;
}

export function applyRunEvent(state: RunWorkspaceState, event: SessionEvent): RunWorkspaceState {
  const runId = runIdFromEvent(event);
  if (!runId) {
    const startsAttempt = event.type === "attempt.created" || event.type === "attempt.started";
    if (startsAttempt || state.pendingSnapshot.events.length || !state.activeRunId) {
      const base = startsAttempt && state.pendingSnapshot.status !== "running"
        ? emptySnapshot(state.sessionId)
        : state.pendingSnapshot;
      return { ...state, pendingSnapshot: applySessionEvent(base, event) };
    }
    const active = state.snapshots[state.activeRunId];
    if (!active) return state;
    return {
      ...state,
      snapshots: {
        ...state.snapshots,
        [state.activeRunId]: applySessionEvent(active, event),
      },
    };
  }
  const existing = state.snapshots[runId] || emptySnapshot(state.sessionId);
  const started = event.type === "swarm.started" ? fromStarted(state.sessionId, event.data) : undefined;
  const snapshot = started
    ? {
      ...started,
      events: [...state.pendingSnapshot.events, ...started.events].slice(-500),
      evidence: state.pendingSnapshot.evidence.items.length
        ? state.pendingSnapshot.evidence
        : started.evidence,
    }
    : applySessionEvent({ ...existing, runId }, event);
  const startedSummary = event.type === "swarm.started" ? summaryFromStarted(state.sessionId, event) : undefined;
  const currentSummary = state.summaries.find((item) => item.run_id === runId);
  const nextSummary: DebateRunSummary = {
    ...(currentSummary || startedSummary || { run_id: runId, session_id: state.sessionId || "" }),
    status: snapshot.status,
    updated_at: new Date().toISOString(),
  };
  return {
    ...state,
    activeRunId: event.type === "swarm.started" ? runId : state.activeRunId || runId,
    summaries: [...state.summaries.filter((item) => item.run_id !== runId), nextSummary].sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || ""))),
    snapshots: { ...state.snapshots, [runId]: snapshot },
    pendingSnapshot: started ? emptySnapshot(state.sessionId) : state.pendingSnapshot,
  };
}

export function selectRun(state: RunWorkspaceState, runId: string): RunWorkspaceState {
  if (!state.summaries.some((summary) => summary.run_id === runId) && !state.snapshots[runId]) return state;
  return { ...state, activeRunId: runId };
}

/** A report-only fallback is useful, but it is not a complete historical run. */
export function needsRunHydration(snapshot: WorkspaceSnapshot | undefined): boolean {
  if (!snapshot) return true;
  return snapshot.tasks.length === 0 && snapshot.events.length <= 1;
}

export function activeSnapshot(state: RunWorkspaceState): WorkspaceSnapshot {
  if (state.pendingSnapshot.events.length) return state.pendingSnapshot;
  return (state.activeRunId && state.snapshots[state.activeRunId]) || emptySnapshot(state.sessionId);
}

export function markActiveRunCancelled(state: RunWorkspaceState): RunWorkspaceState {
  if (!state.activeRunId) {
    return {
      ...state,
      pendingSnapshot: { ...state.pendingSnapshot, status: "cancelled" },
    };
  }
  const snapshot = state.snapshots[state.activeRunId];
  return {
    ...state,
    summaries: state.summaries.map((summary) => (
      summary.run_id === state.activeRunId ? { ...summary, status: "cancelled" } : summary
    )),
    snapshots: snapshot
      ? {
        ...state.snapshots,
        [state.activeRunId]: { ...snapshot, status: "cancelled", lastError: undefined },
      }
      : state.snapshots,
  };
}
