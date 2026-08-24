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

  it("keeps only MCP and database results in the data overview bundle", () => {
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

  it("materializes MCP stage output in the data overview bundle", () => {
    const snapshot = applySessionEvent(emptySnapshot("session-mcp-evidence"), {
      id: "evt-mcp-stage",
      type: "swarm.event",
      data: {
        event: {
          type: "data_service.stage",
          stage: "dataset_catalog",
          status: "completed",
          data: {
            output: { dataset_id: "ds-eurusd", storage_table_name: "fx_bars" },
            source: "mcp",
          },
        },
      },
    });

    expect(snapshot.evidence.items).toHaveLength(1);
    expect(snapshot.evidence.items[0]).toMatchObject({ category: "mcp", title: "数据集目录" });
    expect(snapshot.evidence.items[0].summary).toContain("dataset_id");
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

  it("keeps the parallel analyst and risk reports for the historical report view", () => {
    const snapshot = hydrateRun("session-1", {
      id: "run-agent-reports",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [
        { id: "pair_bull", role: "Pair Bull" },
        { id: "pair_bear", role: "Pair Bear" },
        { id: "macro_technical", role: "Macro + Technical" },
        { id: "fx_risk_officer", role: "FX Risk Officer" },
        { id: "debate_judge", role: "Debate Judge / FX PM" },
      ],
      tasks: [
        { id: "judge", agent_id: "debate_judge", status: "completed", depends_on: ["risk"], summary: "# Final decision" },
        { id: "bull", agent_id: "pair_bull", status: "completed", summary: "# Bull report" },
        { id: "bear", agent_id: "pair_bear", status: "completed", summary: "# Bear report" },
        { id: "macro", agent_id: "macro_technical", status: "completed", summary: "# Macro report" },
        { id: "risk", agent_id: "fx_risk_officer", status: "completed", summary: "# Risk report" },
      ],
      final_report: "# Final decision",
    });

    expect(snapshot.agentReports?.map((item) => item.agentId)).toEqual([
      "pair_bull",
      "pair_bear",
      "macro_technical",
      "fx_risk_officer",
    ]);
    expect(snapshot.agentReports?.map((item) => item.role)).toEqual([
      "多头观点分析师",
      "空头观点分析师",
      "宏观与技术分析师",
      "外汇风险分析师",
    ]);
    expect(snapshot.agentReports?.some((item) => item.agentId === "debate_judge")).toBe(false);
  });

  it("parses a FinalDecision JSON block embedded in the Markdown report", () => {
    const snapshot = hydrateRun("session-1", {
      id: "run-final-decision",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [],
      tasks: [],
      final_report: '# FinalDecision\n\n```json\n{"decision":"wait","confidence":"0.4","scenario_probabilities":{"bull":"0.25","base":"0.45","bear":"0.3"},"trade_plan":{"entry_zone":"null","stop_loss":"null","targets":""},"invalidation_conditions":{"item":["等待 live quote"]}}\n```',
    });

    expect(snapshot.report?.direction).toBe("等待确认");
    expect(snapshot.report?.action).toBe("暂不交易");
    expect(snapshot.report?.entry).toBeUndefined();
    expect(snapshot.report?.takeProfit).toBeUndefined();
    expect(snapshot.report?.probabilities).toMatchObject({ bullish: 0.25, neutral: 0.45, bearish: 0.3 });
    expect(snapshot.report?.invalidation).toEqual(["等待 live quote"]);
    expect(snapshot.report?.markdown).toContain("FinalDecision");
  });

  it("hydrates the degraded presentation summary without inventing price levels", () => {
    const snapshot = hydrateRun("session-presentation", {
      id: "run-presentation",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [],
      tasks: [],
      final_report: JSON.stringify({
        decision: "wait",
        confidence: 0.85,
        trade_plan: { entry_zone: null, stop_loss: null, targets: [] },
        presentation: {
          market_background: "美元历史基本面背景偏强，EUR/USD 宏观背景偏空",
          background_strength: "low",
          technical_confirmation: "无法确认：4H 无数据，1D 仅 21 根（1D 已达到 20 根观察门槛，完整确认仍需 50 根）",
          data_quality: "degraded",
          summary: "宏观背景偏空，但缺少价格和事件确认，不能转化为交易信号",
          usable_evidence: ["US PMI 高于 EU PMI"],
          limitations: ["4H bar_count=0"],
        },
      }),
    });

    expect(snapshot.report?.presentation?.marketBackground).toContain("宏观背景偏空");
    expect(snapshot.report?.presentation?.technicalConfirmation).toContain("4H 无数据");
    expect(snapshot.report?.entry).toBeUndefined();
    expect(snapshot.report?.stopLoss).toBeUndefined();
  });

  it("replays persisted swarm events so historical logs keep tool and MCP layers", () => {
    const snapshot = hydrateRun("session-1", {
      id: "run-history",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [{ id: "pair_bull", role: "Pair Bull" }],
      tasks: [{ id: "bull-task", agent_id: "pair_bull", status: "completed" }],
      events: [
        {
          type: "tool_call",
          agent_id: "pair_bull",
          task_id: "bull-task",
          data: { tool: "get_fx_evidence_manifest", input: { target: "EURUSD" } },
          timestamp: "2026-08-20T00:00:01Z",
        },
        {
          type: "data_service.stage",
          data: { stage: "database_query", status: "completed" },
          timestamp: "2026-08-20T00:00:02Z",
        },
        { type: "run_completed", data: { status: "completed" }, timestamp: "2026-08-20T00:00:03Z" },
      ],
    });

    expect(snapshot.events.some((event) => event.layer === "TOOL" && event.label === "get_fx_evidence_manifest")).toBe(true);
    expect(snapshot.events.some((event) => event.layer === "MCP" && event.label === "database_query")).toBe(true);
    expect(snapshot.status).toBe("completed");
  });

  it("restores the frozen evidence bundle for historical data overview", () => {
    const snapshot = hydrateRun("session-1", {
      id: "run-evidence-history",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [],
      tasks: [],
      evidence_bundle: {
        evidence_context_id: "ctx-history",
        source_name: "database",
        as_of: "2026-08-20T00:00:00Z",
        technical_regime: { timeframes: { "4H": {}, "1D": {} } },
        evidence: [{ evidence_id: "quote-1", domain: "market", name: "EURUSD spot", value: { last: 1.16 }, observation_time: "2026-08-20T00:00:00Z" }],
      },
    });

    expect(snapshot.evidence.source).toBe("database");
    expect(snapshot.evidence.timeframe).toBe("4H/1D");
    expect(snapshot.evidence.items).toHaveLength(1);
    expect(snapshot.evidence.items[0]).toMatchObject({ id: "quote-1", category: "market", title: "EURUSD spot" });
  });

  it("merges persisted MCP evidence with a frozen historical evidence bundle", () => {
    const snapshot = hydrateRun("session-mcp-history", {
      id: "run-mcp-history",
      preset_name: "fx_debate_team",
      status: "completed",
      agents: [],
      tasks: [],
      events: [
        {
          type: "data_service.stage",
          stage: "dataset_catalog",
          status: "completed",
          data: { output: { dataset_id: "ds-eurusd" }, source: "mcp" },
          timestamp: "2026-08-20T00:00:01Z",
        },
      ],
      evidence_bundle: {
        evidence_context_id: "ctx-history-mcp",
        source_name: "database",
        as_of: "2026-08-20T00:00:00Z",
        evidence: [{ evidence_id: "quote-1", domain: "market", name: "EURUSD spot", value: 1.16 }],
      },
    });

    expect(snapshot.evidence.items.map((item) => item.category)).toEqual(["market", "mcp"]);
    expect(snapshot.evidence.items[1]).toMatchObject({ title: "数据集目录", source: "mcp" });
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

  it("materializes the frozen AI-search preview into data overview evidence", () => {
    const snapshot = applySessionEvent(emptySnapshot("session-ai-search"), {
      id: "evt-ai-search-context",
      type: "fx_debate.context_ready",
      data: {
        type: "context_ready",
        data: {
          evidence_context_id: "ctx-ai-search",
          source: "ai_search",
          as_of: "2026-08-20T04:00:00Z",
          data_preview: {
            evidence_context_id: "ctx-ai-search",
            source: "ai_search",
            counts: { market: 1, technical: 1, macro: 1, news: 1 },
            domains: {
              market: { count: 1, rows: [{ evidence_id: "quote-1", name: "spot_quote", value: 1.16 }] },
              technical: { count: 1, rows: [{ evidence_id: "tech-1", name: "rsi", value: 54 }] },
              macro: { count: 1, rows: [{ evidence_id: "macro-1", name: "policy_rate_diff", value: 1.25 }] },
              news: { count: 1, rows: [{ evidence_id: "news-1", name: "headline", title: "ECB outlook" }] },
            },
          },
        },
      },
    });

    expect(snapshot.evidence.source).toBe("ai_search");
    expect(snapshot.evidence.items.map((item) => item.category)).toEqual([
      "market",
      "technical",
      "macro",
      "news",
    ]);
    expect(snapshot.evidence.items[0].raw).toMatchObject({ evidence_id: "quote-1" });
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

  it("shows MCP transport queries in the MCP log layer", () => {
    const snapshot = applySessionEvent(emptySnapshot("session-mcp-query"), {
      id: "evt-mcp-query",
      type: "swarm.event",
      data: {
        event: {
          type: "data_service.query_completed",
          transport: "mcp_stdio",
          tool: "unified_search",
          status: "completed",
        },
      },
    });

    expect(snapshot.events[0].layer).toBe("MCP");
    expect(snapshot.events[0].label).toBe("unified_search");
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
