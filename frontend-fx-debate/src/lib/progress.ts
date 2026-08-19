import type { AgentSnapshot, SwarmTask, WorkspaceEvent, WorkspaceSnapshot } from "@/types";

export type ProgressStageStatus = "pending" | "in_progress" | "completed" | "failed";
export type ProgressStageKind = "route" | "tool" | "context" | "execution" | "result";

export interface ResearchProgressStage {
  id: string;
  kind: ProgressStageKind;
  label: string;
  detail: string;
  status: ProgressStageStatus;
  agentIds: string[];
  taskIds: string[];
}

export interface ResearchProgress {
  stages: ResearchProgressStage[];
  currentLabel: string;
  activeAgents: string[];
}

const ACTIVE = new Set(["running", "retrying", "in_progress"]);
const FAILED = new Set(["failed", "cancelled"]);

function stageStatus(statuses: string[], runStatus: WorkspaceSnapshot["status"]): ProgressStageStatus {
  // A task can finish its last retry before the runtime emits run_completed.
  // Keep that intermediate state informational; red is reserved for a run
  // that the server has actually finalized as failed.
  if (statuses.some((status) => FAILED.has(status))) return runStatus === "failed" ? "failed" : "in_progress";
  if (statuses.length > 0 && statuses.every((status) => status === "completed")) return "completed";
  if (statuses.some((status) => ACTIVE.has(status))) return "in_progress";
  return "pending";
}

function readableIdentifier(value: string): string {
  return value
    .replace(/^task[-_]/, "")
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part) => part.toUpperCase() === "FX" ? "FX" : part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function presetLabel(preset?: string): string {
  return preset ? readableIdentifier(preset) : "协作运行";
}

function agentForTask(task: SwarmTask, agents: AgentSnapshot[]): AgentSnapshot | undefined {
  return agents.find((agent) => agent.taskId === task.id)
    || agents.find((agent) => agent.id === task.agent_id);
}

function liveTaskStatus(task: SwarmTask, agents: AgentSnapshot[]): string {
  return agentForTask(task, agents)?.status || task.status || "pending";
}

function taskLayers(tasks: SwarmTask[]): SwarmTask[][] {
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const memo = new Map<string, number>();

  const depth = (task: SwarmTask, visiting = new Set<string>()): number => {
    const known = memo.get(task.id);
    if (known !== undefined) return known;
    if (visiting.has(task.id)) return 0;
    const nextVisiting = new Set(visiting).add(task.id);
    const dependencies = (task.depends_on || []).map((id) => byId.get(id)).filter(Boolean) as SwarmTask[];
    const value = dependencies.length ? 1 + Math.max(...dependencies.map((dependency) => depth(dependency, nextVisiting))) : 0;
    memo.set(task.id, value);
    return value;
  };

  const grouped = new Map<number, SwarmTask[]>();
  tasks.forEach((task) => {
    const layer = depth(task);
    grouped.set(layer, [...(grouped.get(layer) || []), task]);
  });
  return [...grouped.entries()].sort(([left], [right]) => left - right).map(([, layer]) => layer);
}

function taskStages(workspace: WorkspaceSnapshot): ResearchProgressStage[] {
  return taskLayers(workspace.tasks).map((tasks, index) => {
    const agents = tasks.map((task) => agentForTask(task, workspace.agents));
    const roles = agents.map((agent, taskIndex) => agent?.role || readableIdentifier(tasks[taskIndex].agent_id));
    const taskIds = tasks.map((task) => task.id);
    const agentIds = tasks.map((task, taskIndex) => agents[taskIndex]?.id || task.agent_id);
    return {
      id: `execution-${index}-${taskIds.join("-")}`,
      kind: "execution",
      label: tasks.length === 1 ? roles[0] : `${tasks.length} 个任务并行执行`,
      detail: tasks.length === 1
        ? `${roles[0]}负责本阶段，依赖关系来自服务端运行计划`
        : roles.join("、"),
      status: stageStatus(tasks.map((task) => liveTaskStatus(task, workspace.agents)), workspace.status),
      agentIds: [...new Set(agentIds)],
      taskIds,
    };
  });
}

function resultForTool(events: WorkspaceEvent[], call: WorkspaceEvent): WorkspaceEvent | undefined {
  const callIndex = events.indexOf(call);
  return events.slice(callIndex + 1).find((event) => event.type === "tool_result" && event.label === call.label && !event.agentId && !event.taskId);
}

function toolStageLabel(tool: string): string {
  if (tool === "run_fx_debate") return "创建 FX Debate 协作运行";
  if (tool === "swarm") return "创建多 Agent 协作运行";
  return `调用 ${readableIdentifier(tool)}`;
}

function observedToolStages(workspace: WorkspaceSnapshot): ResearchProgressStage[] {
  const calls = workspace.events.filter((event) => event.type === "tool_call" && !event.agentId && !event.taskId);
  const grouped = new Map<string, WorkspaceEvent[]>();
  calls.forEach((call) => grouped.set(call.label, [...(grouped.get(call.label) || []), call]));
  return [...grouped.entries()].map(([label, groupedCalls], index) => {
    const results = groupedCalls.map((call) => resultForTool(workspace.events, call));
    const returned = results.filter(Boolean) as WorkspaceEvent[];
    const createsRun = label === "run_fx_debate" || label === "swarm";
    const status: ProgressStageStatus = returned.some((result) => result.status === "failed") && workspace.status === "failed"
      ? "failed"
      : returned.length === groupedCalls.length
        ? "completed"
        : createsRun && workspace.runId ? "completed"
          : workspace.status === "failed" ? "failed" : "in_progress";
    const detail = createsRun && workspace.runId
      ? "服务端已生成本次运行计划"
      : returned.length === groupedCalls.length
        ? `${groupedCalls.length} 次调用，已全部返回`
        : `${groupedCalls.length} 次调用，${returned.length ? `${returned.length} 次已返回` : "等待返回"}`;
    return {
      id: `tool-${index}-${label}`,
      kind: "tool",
      label: toolStageLabel(label),
      detail,
      status,
      agentIds: [],
      taskIds: [],
    };
  });
}

function routeStage(workspace: WorkspaceSnapshot): ResearchProgressStage | undefined {
  if (workspace.status === "idle" && workspace.events.length === 0) return undefined;
  const hasSelectedPath = Boolean(workspace.runId)
    || workspace.events.some((event) => event.type === "tool_call")
    || workspace.status === "completed";
  const failedBeforeSelection = workspace.status === "failed" && !hasSelectedPath;
  return {
    id: "route",
    kind: "route",
    label: workspace.runId
      ? `路由至 ${presetLabel(workspace.preset)}`
      : workspace.status === "completed" ? "直接对话处理" : "识别问题并选择处理路径",
    detail: workspace.runId
      ? `当前链路由 ${workspace.preset || "服务端 preset"} 的实际任务依赖生成`
      : hasSelectedPath ? "处理路径已经确定" : "尚未收到服务端路由结果",
    status: failedBeforeSelection ? "failed" : hasSelectedPath ? "completed" : workspace.status === "running" ? "in_progress" : "pending",
    agentIds: [],
    taskIds: [],
  };
}

function contextStages(workspace: WorkspaceSnapshot): ResearchProgressStage[] {
  return workspace.events.filter((event) => event.type === "context_ready").map((event, index) => ({
    id: `context-${index}-${event.id}`,
    kind: "context" as const,
    label: "研究数据上下文就绪",
    detail: "本阶段由后端数据准备事件生成，不代表其他路由也必须经过该步骤",
    status: "completed" as const,
    agentIds: [],
    taskIds: [],
  }));
}

function resultStages(workspace: WorkspaceSnapshot, execution: ResearchProgressStage[]): ResearchProgressStage[] {
  if (workspace.report) {
    return [{
      id: "result",
      kind: "result",
      label: "生成最终结果",
      detail: "服务端已经返回本次运行的最终结果",
      status: "completed",
      agentIds: [],
      taskIds: [],
    }];
  }
  if (workspace.status === "completed" && !workspace.runId) {
    return [{
      id: "result",
      kind: "result",
      label: "生成对话回复",
      detail: "本次请求未启动 Swarm，已按直接对话路径完成",
      status: "completed",
      agentIds: [],
      taskIds: [],
    }];
  }
  if (workspace.status === "running" && execution.length > 0 && execution.every((stage) => stage.status === "completed")) {
    return [{
      id: "result",
      kind: "result",
      label: "汇总执行结果",
      detail: "实际 DAG 已执行完毕，正在等待服务端整理最终响应",
      status: "in_progress",
      agentIds: [],
      taskIds: [],
    }];
  }
  return [];
}

export function buildResearchProgress(workspace: WorkspaceSnapshot): ResearchProgress {
  const route = routeStage(workspace);
  const execution = taskStages(workspace);
  const stages = [
    ...(route ? [route] : []),
    ...observedToolStages(workspace),
    ...contextStages(workspace),
    ...execution,
    ...resultStages(workspace, execution),
  ];
  const current = stages.find((stage) => stage.status === "failed")
    || stages.find((stage) => stage.status === "in_progress")
    || [...stages].reverse().find((stage) => stage.status === "completed");

  return {
    stages,
    currentLabel: workspace.status === "completed"
      ? workspace.runId ? "协作运行已完成" : "处理已完成"
      : workspace.status === "cancelled" ? "运行已取消" : current?.label || "等待输入",
    activeAgents: workspace.agents.filter((agent) => ACTIVE.has(agent.status)).map((agent) => agent.id),
  };
}
