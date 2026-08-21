import { describe, expect, it } from "vitest";
import { activeSnapshot, applyRunEvent, emptyRunWorkspace, hydrateReportsFromMessages, hydrateRunSnapshot, markActiveRunCancelled, needsRunHydration, selectRun } from "@/lib/run_workspace";
import type { SessionEvent } from "@/types";

const started = (runId: string): SessionEvent => ({
  id: `started-${runId}`,
  type: "swarm.started",
  data: {
    run_id: runId,
    preset: "fx_debate_team",
    status: "running",
    agents: [{ id: "pair_bull", role: "Pair Bull" }],
    tasks: [{ id: `${runId}-task`, agent_id: "pair_bull", status: "running" }],
  },
});

describe("RunWorkspaceState", () => {
  it("keeps pre-run Session events visible until the Swarm run id exists", () => {
    let state = emptyRunWorkspace("session-1");
    state = applyRunEvent(state, {
      id: "attempt-started",
      type: "attempt.started",
      data: { attempt_id: "attempt-1" },
    });
    state = applyRunEvent(state, {
      id: "fx-tool-call",
      type: "tool_call",
      data: {
        attempt_id: "attempt-1",
        tool: "run_fx_debate",
        arguments: { target: "EURUSD", timeframe: "2 weeks; 4H/1D" },
      },
    });

    expect(activeSnapshot(state).status).toBe("running");
    expect(activeSnapshot(state).events).toHaveLength(2);
    expect(activeSnapshot(state).variables.target).toBe("EURUSD");

    state = applyRunEvent(state, started("swarm-one"));
    expect(activeSnapshot(state).runId).toBe("swarm-one");
    expect(activeSnapshot(state).events.map((event) => event.type)).toEqual([
      "attempt.started",
      "tool_call",
      "swarm.started",
    ]);
  });

  it("carries the context preview into the run that is announced afterward", () => {
    let state = emptyRunWorkspace("session-ai-search");
    state = applyRunEvent(state, {
      id: "context-ai-search",
      type: "fx_debate.context_ready",
      data: {
        type: "context_ready",
        data: {
          source: "ai_search",
          data_preview: {
            source: "ai_search",
            domains: {
              market: { count: 1, rows: [{ evidence_id: "quote-1", name: "spot_quote", value: 1.16 }] },
            },
          },
        },
      },
    });
    state = applyRunEvent(state, started("swarm-ai-search"));

    expect(activeSnapshot(state).runId).toBe("swarm-ai-search");
    expect(activeSnapshot(state).evidence.source).toBe("ai_search");
    expect(activeSnapshot(state).evidence.items[0]?.id).toBe("quote-1");
  });

  it("streams worker text into the matching Agent snapshot", () => {
    let state = applyRunEvent(emptyRunWorkspace("session-1"), started("swarm-one"));
    state = applyRunEvent(state, {
      id: "worker-text-1",
      type: "swarm.event",
      data: {
        run_id: "swarm-one",
        event: {
          type: "worker_text",
          agent_id: "pair_bull",
          task_id: "swarm-one-task",
          data: { content: "正在核对上行证据" },
        },
      },
    });

    const bull = activeSnapshot(state).agents.find((agent) => agent.id === "pair_bull");
    expect(bull?.status).toBe("in_progress");
    expect(bull?.output).toContain("正在核对上行证据");
    const events = activeSnapshot(state).events;
    expect(events[events.length - 1]?.output).toBe("正在核对上行证据");
  });

  it("uses the terminal status carried by run_completed", () => {
    let state = applyRunEvent(emptyRunWorkspace("session-1"), started("swarm-one"));
    state = applyRunEvent(state, {
      id: "run-failed",
      type: "swarm.event",
      data: {
        run_id: "swarm-one",
        event: { type: "run_completed", data: { status: "failed" } },
      },
    });

    expect(activeSnapshot(state).status).toBe("failed");
  });

  it("keeps a worker retry from appearing as a failed run", () => {
    let state = applyRunEvent(emptyRunWorkspace("session-retry"), {
      type: "swarm.started",
      data: {
        run_id: "retry-run",
        preset: "fx_debate_team",
        status: "running",
        agents: [{ id: "pair_bull", role: "Pair Bull" }],
        tasks: [{ id: "bull", agent_id: "pair_bull", status: "in_progress", depends_on: [] }],
      },
    });
    state = applyRunEvent(state, {
      type: "swarm.event",
      data: { run_id: "retry-run", event: { type: "worker_failed", agent_id: "pair_bull", task_id: "bull", data: { error: "temporary provider timeout" } } },
    });

    expect(activeSnapshot(state).status).toBe("running");
    expect(activeSnapshot(state).agents[0]?.status).toBe("retrying");
    expect(activeSnapshot(state).tasks[0]?.status).toBe("retrying");
    expect(activeSnapshot(state).agents[0]?.error).toBeUndefined();

    state = applyRunEvent(state, {
      type: "swarm.event",
      data: { run_id: "retry-run", event: { type: "task_failed", agent_id: "pair_bull", task_id: "bull", data: { error: "all retries exhausted" } } },
    });
    expect(activeSnapshot(state).agents[0]?.status).toBe("failed");
  });

  it("keeps two runs isolated and selects the newest run", () => {
    let state = emptyRunWorkspace("session-1");
    state = applyRunEvent(state, started("swarm-one"));
    state = applyRunEvent(state, started("swarm-two"));
    expect(state.summaries.map((item) => item.run_id)).toEqual(["swarm-one", "swarm-two"]);
    expect(state.activeRunId).toBe("swarm-two");
    expect(state.snapshots["swarm-one"].events).toHaveLength(1);
    expect(state.snapshots["swarm-two"].events).toHaveLength(1);
  });

  it("routes later events to their own run and can switch back", () => {
    let state = applyRunEvent(emptyRunWorkspace("session-1"), started("swarm-one"));
    state = applyRunEvent(state, started("swarm-two"));
    state = applyRunEvent(state, {
      id: "done-one",
      type: "swarm.event",
      data: { run_id: "swarm-one", event: { type: "run_completed", status: "completed" } },
    });
    expect(state.snapshots["swarm-one"].status).toBe("completed");
    expect(state.snapshots["swarm-two"].status).toBe("running");
    expect(activeSnapshot(selectRun(state, "swarm-one")).runId).toBe("swarm-one");
  });

  it("marks the restored active run cancelled immediately after stop", () => {
    let state = applyRunEvent(emptyRunWorkspace("session-1"), started("swarm-one"));
    state = markActiveRunCancelled(state);
    expect(activeSnapshot(state).status).toBe("cancelled");
    expect(state.summaries[0]?.status).toBe("cancelled");
  });

  it("uses the persisted assistant report when a run record has no report", () => {
    let state = hydrateRunSnapshot(emptyRunWorkspace("session-history"), {
      id: "swarm-history",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [],
      tasks: [],
      final_report: null,
    });
    state = hydrateReportsFromMessages(state, [{
      message_id: "assistant-1",
      session_id: "session-history",
      role: "assistant",
      content: "# EURUSD\n\n方向：等待",
      created_at: "2026-08-20T00:00:00Z",
      metadata: { swarm_run_id: "swarm-history" },
    }]);

    expect(activeSnapshot(state).report?.markdown).toContain("EURUSD");
  });

  it("keeps report-only history fallbacks eligible for durable run hydration", () => {
    let state = hydrateRunSnapshot(emptyRunWorkspace("session-history"), {
      id: "swarm-history",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [],
      tasks: [],
      final_report: "# EURUSD",
    });
    expect(needsRunHydration(state.snapshots["swarm-history"])).toBe(true);
    state = hydrateRunSnapshot(state, {
      id: "swarm-history",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [{ id: "pair_bull", role: "Pair Bull" }],
      tasks: [{ id: "task-1", agent_id: "pair_bull", status: "completed" }],
      events: [{ type: "run_completed", data: { status: "completed" } }],
      final_report: "# EURUSD",
    });
    expect(needsRunHydration(state.snapshots["swarm-history"])).toBe(false);
  });
});
