/* Pure view projection for the FX Debate workbench.
 * The page consumes this small interface instead of understanding every raw
 * runtime event. It is intentionally dependency-free so it can later be
 * moved to the React frontend unchanged.
 */
(function (global) {
  const AGENT_META = {
    pair_bull: { label: "Pair Bull", stage: 1, kind: "hypothesis" },
    pair_bear: { label: "Pair Bear", stage: 1, kind: "hypothesis" },
    macro_technical: { label: "Macro + Technical", stage: 1, kind: "relative_state" },
    fx_risk_officer: { label: "FX Risk Officer", stage: 2, kind: "risk_review" },
    debate_judge: { label: "Debate Judge", stage: 3, kind: "decision" },
  };

  function statusForAgent(events, agentId) {
    const mine = events.filter((event) => event.agent_id === agentId);
    if (mine.some((event) => String(event.type).includes("failed"))) return "failed";
    if (mine.some((event) => event.type === "worker_completed")) return "completed";
    if (mine.some((event) => event.type === "worker_started" || event.type === "tool_call")) return "running";
    return "waiting";
  }

  function operationForAgent(events, agentId) {
    const mine = events.filter((event) => event.agent_id === agentId);
    const last = mine[mine.length - 1];
    if (!last) return "等待调度";
    const data = last.data || {};
    if (last.type === "tool_call") return `调用 ${data.tool || "Tool"}`;
    if (last.type === "worker_completed") return "输出已验证";
    if (String(last.type).includes("failed")) return data.error || "运行失败";
    if (last.type === "worker_started") return "准备模型输入";
    return data.operation || String(last.type).replaceAll("_", " ");
  }

  function projectFxRun(events, result) {
    const agents = Object.entries(AGENT_META).map(([id, meta]) => ({
      id,
      ...meta,
      status: statusForAgent(events, id),
      operation: operationForAgent(events, id),
      eventCount: events.filter((event) => event.agent_id === id).length,
    }));
    const completed = agents.filter((agent) => agent.status === "completed").length;
    const context = events.find((event) => event.type === "worker_started")?.data?.input?.user_prompt || "";
    return {
      agents,
      completed,
      total: agents.length,
      currentStage: agents.some((agent) => agent.id === "debate_judge" && agent.status !== "waiting")
        ? 3
        : agents.some((agent) => agent.id === "fx_risk_officer" && agent.status !== "waiting")
          ? 2
          : 1,
      result: result || null,
      contextHint: context,
    };
  }

  function extractJson(text) {
    if (!text || typeof text !== "string") return null;
    const match = text.match(/```json\s*([\s\S]*?)\s*```/i);
    try {
      const value = JSON.parse(match ? match[1] : text);
      return value && typeof value === "object" ? value : null;
    } catch (_error) {
      return null;
    }
  }

  function artifactCards(event) {
    const data = event?.data || {};
    const rawOutput = data.output ?? data.result ?? data.result_preview;
    const output =
      rawOutput && typeof rawOutput === "string"
        ? extractJson(rawOutput) || rawOutput
        : rawOutput;
    const cards = [];
    if (output && typeof output === "object") {
      if (output.valid !== undefined) cards.push({ label: "验证状态", value: output.valid ? "通过" : "未通过", tone: output.valid ? "good" : "bad" });
      if (output.mode) cards.push({ label: "契约", value: output.mode, tone: "neutral" });
      if (output.hypothesis_status) cards.push({ label: "假设状态", value: output.hypothesis_status, tone: "neutral" });
      if (output.strength) cards.push({ label: "强度", value: output.strength, tone: "neutral" });
      if (output.relative_macro_state) cards.push({ label: "相对宏观", value: output.relative_macro_state, tone: "neutral" });
      if (output.technical_state) cards.push({ label: "技术状态", value: output.technical_state, tone: "neutral" });
      if (output.cross_confirmation) cards.push({ label: "交叉确认", value: output.cross_confirmation, tone: "neutral" });
      if (output.risk_level) cards.push({ label: "风险等级", value: output.risk_level, tone: "warn" });
      if (output.decision) cards.push({ label: "最终决策", value: output.decision, tone: output.decision === "wait" ? "warn" : "good" });
      if (output.confidence !== undefined) cards.push({ label: "置信度", value: `${Math.round(Number(output.confidence) * 100)}%`, tone: "neutral" });
    }
    return cards;
  }

  function parseEventJson(value) {
    if (value && typeof value === "object") return value;
    if (typeof value !== "string") return value;
    try { return JSON.parse(value); } catch (_error) { return value; }
  }

  function buildDiagnostics(events) {
    const validatorEvents = new Map();
    for (const event of events || []) {
      const data = event.data || {};
      if (event.type !== "tool_result" || data.tool !== "validate_fx_output") continue;
      const output = parseEventJson(data.output);
      if (!output || typeof output !== "object" || output.valid !== false) continue;
      const key = `${event.agent_id || ""}:${event.task_id || ""}`;
      const list = validatorEvents.get(key) || [];
      list.push({ event, output });
      validatorEvents.set(key, list);
    }
    const failureTypes = new Set(["worker_failed", "worker_timeout", "worker_incomplete", "task_failed", "run_error", "run_recovered"]);
    const result = [];
    for (const event of events || []) {
      const data = event.data || {};
      const message = String(data.error || data.reason || "");
      const contract = data.error_kind === "fx_validation_contract" || message.includes("FX validation contract not met");
      if (!failureTypes.has(event.type) && !contract) continue;
      if (["task_failed", "run_error"].includes(event.type) && result.some((item) => item.task_id === event.task_id && item.message === message)) continue;
      const key = `${event.agent_id || ""}:${event.task_id || ""}`;
      const related = (validatorEvents.get(key) || [])
        .filter((item) => Number(item.event.sequence || 0) <= Number(event.sequence || 0))
        .slice(-3);
      const validation = data.validation || related.at(-1)?.output || {};
      const errors = Array.isArray(validation.errors) ? validation.errors : [];
      const retries = (events || []).filter((item) =>
        item.type === "task_retry" && item.agent_id === event.agent_id && item.task_id === event.task_id && Number(item.sequence || 0) <= Number(event.sequence || 0),
      ).length;
      result.push({
        diagnostic_id: `diag-${event.sequence}`,
        severity: "error",
        title: contract ? "FX 契约校验失败" : "运行失败",
        message: message || "运行阶段未提供错误描述。",
        error_kind: data.error_kind || event.type,
        phase: data.phase || event.type,
        agent_id: event.agent_id,
        task_id: event.task_id,
        sequence: event.sequence,
        timestamp: event.timestamp,
        iteration: data.iterations || data.iteration,
        validation_errors: errors.slice(0, 20),
        related_sequences: related.map((item) => item.event.sequence),
        retry_count: retries,
      });
    }
    return result;
  }

  global.FxRunView = { AGENT_META, projectFxRun, extractJson, artifactCards, buildDiagnostics };
})(window);
