import { describe, expect, it } from "vitest";
import { buildResearchProgress, dependencyAwareAgentStatus } from "@/lib/progress";
import { applyRunEvent, emptyRunWorkspace } from "@/lib/run_workspace";
import type { WorkspaceSnapshot } from "@/types";

describe("dynamic research progress model", () => {
  it("shows only stages observed before the service returns a run plan", () => {
    let state = emptyRunWorkspace("session-1");
    state = applyRunEvent(state, { type: "attempt.started", data: { attempt_id: "a1" } });
    state = applyRunEvent(state, {
      type: "tool_call",
      data: { tool: "run_fx_debate", arguments: { target: "EURUSD" } },
    });

    const progress = buildResearchProgress(state.pendingSnapshot);
    expect(progress.stages.map((stage) => stage.label)).toEqual([
      "识别问题并选择处理路径",
      "创建 FX Debate 协作运行",
    ]);
    expect(progress.stages.map((stage) => stage.status)).toEqual(["completed", "in_progress"]);
    expect(progress.currentLabel).toBe("创建 FX Debate 协作运行");
  });

  it("does not treat an intermediate report payload as the final result", () => {
    let state = emptyRunWorkspace("session-intermediate-report");
    state = applyRunEvent(state, { type: "attempt.started", data: { attempt_id: "a1" } });
    state = applyRunEvent(state, {
      type: "tool_call",
      data: { tool: "run_fx_debate", arguments: { target: "EURUSD" } },
    });
    state = applyRunEvent(state, {
      type: "tool_progress",
      data: { report: { direction: "等待确认" } },
    });

    const progress = buildResearchProgress(state.pendingSnapshot);
    expect(progress.stages.some((stage) => stage.kind === "result")).toBe(false);
    expect(progress.currentLabel).toBe("创建 FX Debate 协作运行");
  });

  it("derives FX execution layers from the real task dependencies", () => {
    let state = emptyRunWorkspace("session-1");
    state = applyRunEvent(state, {
      type: "swarm.started",
      data: {
        run_id: "swarm-one",
        preset: "fx_debate_team",
        status: "running",
        agents: [
          { id: "pair_bull", role: "Pair Bull" },
          { id: "pair_bear", role: "Pair Bear" },
          { id: "macro_technical", role: "Macro + Technical" },
          { id: "fx_risk_officer", role: "FX Risk Officer" },
          { id: "debate_judge", role: "Debate Judge" },
        ],
        tasks: [
          { id: "bull", agent_id: "pair_bull", status: "in_progress", depends_on: [] },
          { id: "bear", agent_id: "pair_bear", status: "in_progress", depends_on: [] },
          { id: "macro", agent_id: "macro_technical", status: "pending", depends_on: [] },
          { id: "risk", agent_id: "fx_risk_officer", status: "pending", depends_on: ["bull", "bear", "macro"] },
          { id: "judge", agent_id: "debate_judge", status: "pending", depends_on: ["risk"] },
        ],
      },
    });

    const progress = buildResearchProgress(state.snapshots["swarm-one"]);
    const execution = progress.stages.filter((stage) => stage.kind === "execution");
    expect(execution).toHaveLength(3);
    expect(execution[0].taskIds).toEqual(["bull", "bear", "macro"]);
    expect(execution[1].taskIds).toEqual(["risk"]);
    expect(execution[2].taskIds).toEqual(["judge"]);
    expect(execution[2].label).toBe("辩论裁决与外汇组合经理");
    expect(execution[0].status).toBe("in_progress");
    expect(progress.activeAgents).toEqual(["pair_bull", "pair_bear"]);
    expect(progress.currentLabel).toBe("3 个任务并行执行");
  });

  it("condenses repeated tool calls into one progress summary per tool", () => {
    let state = emptyRunWorkspace("session-tools");
    state = applyRunEvent(state, { type: "attempt.started", data: { attempt_id: "a1" } });
    for (const tool of ["Web Search", "Read Url", "Web Search", "Read Url", "Web Search"]) {
      state = applyRunEvent(state, { type: "tool_call", data: { tool, arguments: {} } });
    }

    const toolStages = buildResearchProgress(state.pendingSnapshot).stages.filter((stage) => stage.kind === "tool");
    expect(toolStages.map((stage) => stage.label)).toEqual(["调用 Web Search", "调用 Read Url"]);
    expect(toolStages.map((stage) => stage.detail)).toEqual(["3 次调用，等待返回", "2 次调用，等待返回"]);
  });

  it("builds a different chain for a generic two-step swarm", () => {
    let state = emptyRunWorkspace("session-2");
    state = applyRunEvent(state, {
      type: "swarm.started",
      data: {
        run_id: "generic-one",
        preset: "document_review_team",
        status: "running",
        agents: [
          { id: "extractor", role: "材料提取" },
          { id: "reviewer", role: "合规复核" },
        ],
        tasks: [
          { id: "extract", agent_id: "extractor", status: "completed", depends_on: [] },
          { id: "review", agent_id: "reviewer", status: "in_progress", depends_on: ["extract"] },
        ],
      },
    });

    const progress = buildResearchProgress(state.snapshots["generic-one"]);
    const execution = progress.stages.filter((stage) => stage.kind === "execution");
    expect(execution.map((stage) => stage.label)).toEqual(["材料提取", "合规复核"]);
    expect(execution.map((stage) => stage.agentIds)).toEqual([["extractor"], ["reviewer"]]);
    expect(progress.stages.some((stage) => stage.label.includes("FX"))).toBe(false);
    expect(progress.currentLabel).toBe("合规复核");
  });

  it("treats downstream blocked tasks as waiting for dependencies, not failed", () => {
    let state = emptyRunWorkspace("session-blocked");
    state = applyRunEvent(state, {
      type: "swarm.started",
      data: {
        run_id: "blocked-run",
        preset: "fx_debate_team",
        status: "running",
        agents: [
          { id: "researcher", role: "Researcher" },
          { id: "risk", role: "Risk Officer" },
        ],
        tasks: [
          { id: "research", agent_id: "researcher", status: "in_progress", depends_on: [] },
          { id: "risk-check", agent_id: "risk", status: "blocked", depends_on: ["research"] },
        ],
      },
    });

    const execution = buildResearchProgress(state.snapshots["blocked-run"]).stages
      .filter((stage) => stage.kind === "execution");
    expect(execution.map((stage) => stage.status)).toEqual(["in_progress", "pending"]);
  });

  it("does not turn an in-flight task failure into a red stage before run completion", () => {
    let state = emptyRunWorkspace("session-terminal-boundary");
    state = applyRunEvent(state, {
      type: "swarm.started",
      data: {
        run_id: "boundary-run",
        preset: "fx_debate_team",
        status: "running",
        agents: [{ id: "researcher", role: "Researcher" }],
        tasks: [{ id: "research", agent_id: "researcher", status: "in_progress", depends_on: [] }],
      },
    });
    state = applyRunEvent(state, {
      type: "swarm.event",
      data: { run_id: "boundary-run", event: { type: "task_failed", agent_id: "researcher", task_id: "research", data: { error: "last attempt failed" } } },
    });

    expect(buildResearchProgress(state.snapshots["boundary-run"]).stages.find((stage) => stage.kind === "execution")?.status).toBe("in_progress");

    state = applyRunEvent(state, {
      type: "swarm.event",
      data: { run_id: "boundary-run", event: { type: "run_completed", data: { status: "failed" } } },
    });
    expect(buildResearchProgress(state.snapshots["boundary-run"]).stages.find((stage) => stage.kind === "execution")?.status).toBe("failed");
  });

  it("keeps a downstream stage from appearing complete while its dependency is still running", () => {
    let state = emptyRunWorkspace("session-ordering");
    state = applyRunEvent(state, {
      type: "swarm.started",
      data: {
        run_id: "ordering-run",
        preset: "fx_debate_team",
        status: "running",
        agents: [
          { id: "research", role: "Research" },
          { id: "risk", role: "Risk" },
          { id: "judge", role: "Judge" },
        ],
        tasks: [
          { id: "research-task", agent_id: "research", status: "in_progress", depends_on: [] },
          { id: "risk-task", agent_id: "risk", status: "in_progress", depends_on: ["research-task"] },
          // A late completion event may arrive before the dependency event.
          { id: "judge-task", agent_id: "judge", status: "completed", depends_on: ["risk-task"] },
        ],
      },
    });

    const execution = buildResearchProgress(state.snapshots["ordering-run"]).stages
      .filter((stage) => stage.kind === "execution");
    expect(execution.map((stage) => stage.status)).toEqual(["in_progress", "in_progress", "pending"]);
    const judge = state.snapshots["ordering-run"].agents.find((agent) => agent.id === "judge");
    expect(dependencyAwareAgentStatus(state.snapshots["ordering-run"], judge!)).toBe("pending");
  });

  it("keeps a later layer pending when another task in the previous layer is still active", () => {
    let state = emptyRunWorkspace("session-layer-barrier");
    state = applyRunEvent(state, {
      type: "swarm.started",
      data: {
        run_id: "layer-barrier-run",
        preset: "document_review_team",
        status: "running",
        agents: [
          { id: "source", role: "Source" },
          { id: "review", role: "Review" },
          { id: "audit", role: "Audit" },
          { id: "publish", role: "Publish" },
        ],
        tasks: [
          { id: "source-task", agent_id: "source", status: "completed", depends_on: [] },
          { id: "review-task", agent_id: "review", status: "completed", depends_on: ["source-task"] },
          { id: "audit-task", agent_id: "audit", status: "in_progress", depends_on: ["source-task"] },
          // The publish task depends on review, but it must still wait for the
          // unresolved audit task in the same preceding execution layer.
          { id: "publish-task", agent_id: "publish", status: "completed", depends_on: ["review-task"] },
        ],
      },
    });

    const execution = buildResearchProgress(state.snapshots["layer-barrier-run"]).stages
      .filter((stage) => stage.kind === "execution");
    expect(execution.map((stage) => stage.status)).toEqual(["completed", "in_progress", "pending"]);
  });

  it("does not let stale completed agent cards outrun live task dependencies", () => {
    const snapshot: WorkspaceSnapshot = {
      sessionId: "session-stale-agent-status",
      runId: "stale-agent-status-run",
      status: "running",
      preset: "fx_debate_team",
      variables: {},
      agents: [
        // These are deliberately stale per-agent events. The task DAG is the
        // fresher source of truth while risk is still executing.
        { id: "research", role: "Research", taskId: "research-task", status: "completed" },
        { id: "risk", role: "Risk", taskId: "risk-task", status: "completed" },
        { id: "judge", role: "Judge", taskId: "judge-task", status: "completed" },
      ],
      tasks: [
        { id: "research-task", agent_id: "research", status: "completed", depends_on: [] },
        { id: "risk-task", agent_id: "risk", status: "in_progress", depends_on: ["research-task"] },
        { id: "judge-task", agent_id: "judge", status: "pending", depends_on: ["risk-task"] },
      ],
      events: [],
      evidence: { items: [] },
    };

    const execution = buildResearchProgress(snapshot).stages.filter((stage) => stage.kind === "execution");
    expect(execution.map((stage) => stage.status)).toEqual(["completed", "in_progress", "pending"]);
    expect(dependencyAwareAgentStatus(snapshot, snapshot.agents[1])).toBe("in_progress");
    expect(dependencyAwareAgentStatus(snapshot, snapshot.agents[2])).toBe("pending");
  });

  it("reconciles stale task snapshots when the server has completed the run", () => {
    const snapshot: WorkspaceSnapshot = {
      sessionId: "session-terminal-snapshot",
      runId: "terminal-snapshot-run",
      status: "completed",
      preset: "fx_debate_team",
      variables: {},
      agents: [],
      tasks: [
        { id: "research-task", agent_id: "research", status: "in_progress", depends_on: [] },
        { id: "risk-task", agent_id: "risk", status: "pending", depends_on: ["research-task"] },
        { id: "judge-task", agent_id: "judge", status: "pending", depends_on: ["risk-task"] },
      ],
      events: [],
      evidence: { items: [] },
      report: { direction: "等待确认" },
    };

    const execution = buildResearchProgress(snapshot).stages.filter((stage) => stage.kind === "execution");
    expect(execution.map((stage) => stage.status)).toEqual(["completed", "completed", "completed"]);
  });

  it("uses a terminal label after a run completes", () => {
    const snapshot: WorkspaceSnapshot = {
      sessionId: "session-1",
      runId: "swarm-one",
      status: "completed",
      preset: "fx_debate_team",
      variables: {},
      agents: [],
      tasks: [],
      events: [],
      evidence: { items: [] },
    };

    expect(buildResearchProgress(snapshot).currentLabel).toBe("协作运行已完成");
  });
});
