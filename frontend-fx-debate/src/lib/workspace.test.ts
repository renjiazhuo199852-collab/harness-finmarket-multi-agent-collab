import { describe, expect, it } from "vitest";
import { applySessionEvent, emptySnapshot, fromStarted, hydrateHistoricalMessage, hydrateRun, isFxPreset } from "@/lib/workspace";

describe("FX workspace event reducer", () => {
  it("hydrates only the agents and tasks returned by swarm.started", () => {
    const snapshot = fromStarted("session-1", {
      run_id: "run-1",
      preset: "fx_debate_team",
      status: "running",
      variables: { target: "EURUSD", timeframe: "2 weeks" },
      agents: [
        { id: "pair_bull", role: "Pair Bull" },
        { id: "pair_bear", role: "Pair Bear" },
      ],
      tasks: [{ id: "bull-task", agent_id: "pair_bull", status: "pending" }],
    });
    expect(snapshot.runId).toBe("run-1");
    expect(snapshot.agents).toHaveLength(2);
    expect(snapshot.agents[0].status).toBe("pending");
    expect(snapshot.tasks).toHaveLength(1);
    expect(snapshot.variables.target).toBe("EURUSD");
    expect(isFxPreset(snapshot)).toBe(true);
  });

  it("updates an agent and records tool input/output", () => {
    let snapshot = fromStarted("session-1", {
      run_id: "run-1",
      preset: "fx_debate_team",
      agents: [{ id: "pair_bull", role: "Pair Bull" }],
      tasks: [{ id: "bull-task", agent_id: "pair_bull", status: "pending" }],
    });
    snapshot = applySessionEvent(snapshot, {
      id: "evt-1",
      type: "swarm.event",
      data: {
        run_id: "run-1",
        event: { type: "worker_started", agent_id: "pair_bull", task_id: "bull-task", timestamp: "2026-01-01T00:00:00Z" },
      },
    });
    snapshot = applySessionEvent(snapshot, {
      id: "evt-2",
      type: "tool_call",
      data: { tool: "get_fx_market_evidence", agent_id: "pair_bull", task_id: "bull-task", input: { target: "EURUSD" } },
    });
    snapshot = applySessionEvent(snapshot, {
      id: "evt-3",
      type: "tool_result",
      data: { tool: "get_fx_market_evidence", agent_id: "pair_bull", task_id: "bull-task", status: "ok", output: { bars: 20 } },
    });
    expect(snapshot.agents[0].status).toBe("in_progress");
    expect(snapshot.agents[0].tool).toBe("get_fx_market_evidence");
    expect(snapshot.events).toHaveLength(4);
    expect(snapshot.evidence.items).toHaveLength(0);
  });

  it("labels transient worker failures as retrying in the event log", () => {
    const snapshot = applySessionEvent(
      fromStarted("session-1", { run_id: "run-retry", preset: "fx_debate_team", agents: [{ id: "pair_bull", role: "Pair Bull" }], tasks: [{ id: "bull", agent_id: "pair_bull", status: "in_progress" }] }),
      {
        id: "evt-worker-retry",
        type: "swarm.event",
        data: { run_id: "run-retry", event: { type: "worker_failed", agent_id: "pair_bull", task_id: "bull", data: { error: "temporary timeout" } } },
      },
    );
    expect(snapshot.events[snapshot.events.length - 1]?.status).toBe("retrying");
    expect(snapshot.agents[0]?.status).toBe("retrying");
    expect(snapshot.status).toBe("running");
  });

  it("keeps only SDK and database results in the data overview bundle", () => {
    let snapshot = emptySnapshot("session-data-filter");
    snapshot = applySessionEvent(snapshot, {
      id: "evt-agent-output",
      type: "swarm.event",
      data: { event: { type: "worker_text", agent_id: "analyst", data: { content: "analysis text", output: "analysis text" } } },
    });
    snapshot = applySessionEvent(snapshot, {
      id: "evt-sdk-output",
      type: "swarm.event",
      data: { event: { type: "data_service.query_completed", tool: "market_bars_search", data: { output: { rows: 20 }, source: "excel" } } },
    });
    snapshot = applySessionEvent(snapshot, {
      id: "evt-db-output",
      type: "swarm.event",
      data: { event: { type: "database.query_completed", data: { output: { row_count: 3 }, source: "postgresql" } } },
    });

    expect(snapshot.evidence.items.map((item) => item.category)).toEqual(["sdk", "database"]);
  });

  it("hydrates a completed run and preserves raw markdown", () => {
    const snapshot = hydrateRun("session-1", {
      id: "run-2",
      preset_name: "fx_debate_team",
      status: "completed",
      user_vars: { target: "EUR/USD" },
      agents: [{ id: "pair_bull", role: "Pair Bull" }],
      tasks: [{ id: "bull-task", agent_id: "pair_bull", status: "completed", summary: "## 上行假设\n\n结论：证据不足。" }],
      final_report: "# EURUSD\n\n方向：震荡",
    });
    expect(snapshot.status).toBe("completed");
    expect(snapshot.report?.markdown).toContain("EURUSD");
    expect(snapshot.events).toHaveLength(2);
    expect(snapshot.events[1].output).toContain("上行假设");
  });

  it("extracts a report nested in the run_fx_debate tool result", () => {
    const snapshot = applySessionEvent(
      fromStarted("session-1", { run_id: "run-3", preset: "fx_debate_team", agents: [], tasks: [] }),
      {
        id: "evt-report",
        type: "tool_result",
        data: {
          tool: "run_fx_debate",
          status: "ok",
          output: JSON.stringify({ final_report: "方向：偏多\n\n风险：突破失败" }),
        },
      },
    );
    expect(snapshot.report?.markdown).toContain("偏多");
  });

  it("keeps the FX evidence-context event in the SDK log layer", () => {
    const snapshot = applySessionEvent(emptySnapshot("session-1"), {
      id: "evt-context",
      type: "fx_debate.context_ready",
      data: {
        type: "context_ready",
        data: { evidence_context_id: "ctx-1", source: "database" },
      },
    });

    expect(snapshot.events[0].layer).toBe("SDK");
    expect(snapshot.events[0].type).toBe("context_ready");
  });

  it("shows independent data-service queries in the SDK log layer", () => {
    const snapshot = applySessionEvent(emptySnapshot("session-1"), {
      id: "evt-data-service",
      type: "swarm.event",
      data: {
        event: {
          type: "data_service.query_completed",
          tool: "market_bars_search",
          status: "completed",
          output: { row_count: 42 },
        },
      },
    });

    expect(snapshot.events[0].layer).toBe("SDK");
    expect(snapshot.events[0].label).toBe("market_bars_search");
  });

  it("shows real MCP stages with their complete trace metadata", () => {
    const snapshot = applySessionEvent(emptySnapshot("session-mcp"), {
      id: "evt-mcp-stage",
      type: "data_service.stage",
      data: {
        type: "mcp_stage",
        trace_id: "trace-1",
        sequence: 3,
        stage: "dataset_catalog",
        status: "completed",
        input: { query: "查询 EURUSD 新闻" },
        output: { dataset_id: "LSEG_NEWS", storage_table_name: "news_articles" },
        duration_ms: 12.5,
        error: null,
      },
    });

    expect(snapshot.events[0].layer).toBe("MCP");
    expect(snapshot.events[0].type).toBe("mcp_stage");
    expect(snapshot.events[0].stage).toBe("dataset_catalog");
    expect(snapshot.events[0].traceId).toBe("trace-1");
    expect(snapshot.events[0].sequence).toBe(3);
    expect(snapshot.events[0].durationMs).toBe(12.5);
    expect(snapshot.events[0].input).toEqual({ query: "查询 EURUSD 新闻" });
    expect(snapshot.events[0].output).toEqual({ dataset_id: "LSEG_NEWS", storage_table_name: "news_articles" });
  });

  it("does not invent state for heartbeat events", () => {
    const snapshot = emptySnapshot("session-1");
    const next = applySessionEvent(snapshot, { type: "heartbeat", data: {} });
    expect(next).toEqual(snapshot);
  });

  it("keeps the real FX risk agent addressable when started data is partial", () => {
    const snapshot = fromStarted("session-1", {
      run_id: "run-4",
      preset: "fx_debate_team",
      agents: [{ id: "pair_bull", role: "Pair Bull" }],
      tasks: [{ id: "risk-task", agent_id: "fx_risk_officer", status: "pending" }],
    });

    expect(snapshot.agents.map((agent) => agent.id)).toContain("fx_risk_officer");

    const next = applySessionEvent(snapshot, {
      id: "evt-risk-heartbeat",
      type: "swarm.event",
      data: {
        event: {
          type: "task_heartbeat",
          agent_id: "fx_risk_officer",
          task_id: "risk-task",
          data: { tool: "llm:default" },
        },
      },
    });

    expect(next.agents.find((agent) => agent.id === "fx_risk_officer")?.status).toBe("in_progress");
  });

  it("does not invent a task topology from a completed historical assistant message", () => {
    const snapshot = hydrateHistoricalMessage("session-history", {
      message_id: "message-1",
      session_id: "session-history",
      role: "assistant",
      content: "## EURUSD 研究结论\n\n方向：等待实时数据确认。",
      created_at: "2026-08-18T06:08:26Z",
      linked_attempt_id: "attempt-1",
      metadata: { run_id: "20260818_135709_47_525b5a" },
    });

    expect(snapshot.status).toBe("completed");
    expect(snapshot.agents).toHaveLength(0);
    expect(snapshot.tasks).toHaveLength(0);
    expect(snapshot.report?.markdown).toContain("EURUSD 研究结论");
  });

  it("preserves dependencies and updates the matching task from live events", () => {
    let snapshot = fromStarted("session-1", {
      run_id: "run-dag",
      preset: "generic_team",
      agents: [
        { id: "researcher", role: "Researcher" },
        { id: "reviewer", role: "Reviewer" },
      ],
      tasks: [
        { id: "research", agent_id: "researcher", status: "completed", depends_on: [] },
        { id: "review", agent_id: "reviewer", status: "pending", depends_on: ["research"] },
      ],
    });

    snapshot = applySessionEvent(snapshot, {
      id: "evt-review-started",
      type: "swarm.event",
      data: { event: { type: "task_started", agent_id: "reviewer", task_id: "review" } },
    });

    expect(snapshot.tasks[1].depends_on).toEqual(["research"]);
    expect(snapshot.tasks[1].status).toBe("in_progress");
  });
});
