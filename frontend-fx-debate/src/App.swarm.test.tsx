import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { clearApiConfig } from "@/lib/api_config";
import { presetDisplay } from "@/lib/swarmZhCN";

const presetList = [
  {
    name: "fx_debate_team",
    title: "FX Debate Team",
    description: "Five-agent FX debate.",
    agent_count: 5,
    variables: ["target", "timeframe", "goal"],
    source: "bundled",
  },
  {
    name: "commodity_research_team",
    title: "Commodity Research Team",
    description: "Supply and demand workflow.",
    agent_count: 3,
    variables: ["commodity", "horizon"],
    source: "bundled",
  },
  ...Array.from({ length: 29 }, (_, index) => ({
    name: `professional_team_${index + 1}`,
    title: `Professional Team ${index + 1}`,
    description: "Professional research workflow.",
    agent_count: 2,
    variables: ["market"],
    source: "bundled",
  })),
  {
    name: "fx_pair_debate_desk_smoke",
    title: "FX Pair Debate Desk Smoke Test",
    description: "Startup smoke preset.",
    agent_count: 1,
    variables: [],
    source: "user",
  },
];

const fxPresetDetail = {
  name: "fx_debate_team",
  title: "FX Debate Team",
  description: "Five-agent FX debate.",
  valid: true,
  errors: [],
  warnings: [],
  variables: ["target", "timeframe", "goal"],
  used_variables: ["target"],
  source: "bundled",
  agents: [
    { id: "pair_bull", role: "Pair Bull", tools: ["get_fx_evidence_manifest"], skills: ["fx-hypothesis-falsification"] },
    { id: "pair_bear", role: "Pair Bear", tools: [], skills: ["fx-hypothesis-falsification"] },
    { id: "macro_technical", role: "Macro Technical Analyst", tools: ["get_fx_evidence_manifest"], skills: ["fx-relative-macro-interpretation", "fx-regime-cross-confirmation"] },
    { id: "fx_risk_officer", role: "FX Risk Officer", tools: [], skills: ["fx-hypothesis-falsification", "fx-relative-macro-interpretation", "fx-regime-cross-confirmation", "risk-analysis"] },
    { id: "debate_judge", role: "Debate Judge", tools: [], skills: ["fx-relative-macro-interpretation", "fx-regime-cross-confirmation", "risk-analysis", "hedging-strategy"] },
  ],
  tasks: [
    { id: "task-pair-bull", agent_id: "pair_bull", depends_on: [], input_from: {} },
    { id: "task-pair-bear", agent_id: "pair_bear", depends_on: [], input_from: {} },
    { id: "task-macro-technical", agent_id: "macro_technical", depends_on: [], input_from: {} },
    { id: "task-risk", agent_id: "fx_risk_officer", depends_on: ["task-pair-bull", "task-pair-bear", "task-macro-technical"], input_from: { upstream: "task-pair-bull" } },
    { id: "task-judge", agent_id: "debate_judge", depends_on: ["task-risk"], input_from: { upstream: "task-risk" } },
  ],
  layers: [
    [
      { task_id: "task-pair-bull", agent_id: "pair_bull" },
      { task_id: "task-pair-bear", agent_id: "pair_bear" },
      { task_id: "task-macro-technical", agent_id: "macro_technical" },
    ],
    [{ task_id: "task-risk", agent_id: "fx_risk_officer" }],
    [{ task_id: "task-judge", agent_id: "debate_judge" }],
  ],
};

const commodityPresetDetail = {
  name: "commodity_research_team",
  title: "Commodity Research Team",
  description: "Supply and demand workflow.",
  valid: true,
  errors: [],
  warnings: [],
  variables: ["commodity", "horizon"],
  used_variables: ["commodity"],
  source: "bundled",
  agents: [
    { id: "supply_analyst", role: "Supply Analyst", tools: ["read_file"], skills: ["commodity-analysis"] },
    { id: "cycle_strategist", role: "Cycle Strategist", tools: [], skills: ["portfolio-construction"] },
  ],
  tasks: [
    { id: "task-supply-research", agent_id: "supply_analyst", depends_on: [], input_from: {} },
    { id: "task-decision", agent_id: "cycle_strategist", depends_on: ["task-supply-research"], input_from: { upstream: "task-supply-research" } },
  ],
  layers: [
    [{ task_id: "task-supply-research", agent_id: "supply_analyst" }],
    [{ task_id: "task-decision", agent_id: "cycle_strategist" }],
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(JSON.stringify(body)),
  } as Response;
}

function installFetchMock(
  handler: (path: string) => Response | Promise<Response>,
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((input: RequestInfo | URL) => handler(String(input)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Swarm catalog UI", () => {
  beforeEach(() => {
    clearApiConfig();
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState({}, "", "/?view=swarm");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    clearApiConfig();
  });

  it("shows a loading state while preset metadata is pending", () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return new Promise<Response>(() => undefined);
      if (path === "/swarm/presets") return new Promise<Response>(() => undefined);
      if (path === "/live") return new Promise<Response>(() => undefined);
      return jsonResponse({}, 404);
    });

    render(<App />);

    expect(screen.getByText("正在读取智能体团队")).toBeTruthy();
  });

  it("hides smoke metadata and presents the filtered professional catalog", async () => {
    const fetchMock = installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/swarm/presets") return jsonResponse(presetList);
      if (path === "/swarm/presets/fx_debate_team") return jsonResponse(fxPresetDetail);
      if (path === "/swarm/presets/commodity_research_team") return jsonResponse(commodityPresetDetail);
      if (path.startsWith("/swarm/presets/professional_team_")) return jsonResponse({ name: path.slice(path.lastIndexOf("/") + 1), agents: [], tasks: [], layers: [] });
      return jsonResponse({}, 404);
    });

    const { container } = render(<App />);

    await screen.findAllByRole("button", { name: /外汇多智能体辩论团队/ });
    expect(screen.getAllByText("智能体中心").length).toBeGreaterThan(0);
    expect(screen.getAllByText("项目核心").length).toBeGreaterThan(0);
    expect(screen.queryByText("平台内置")).toBeNull();
    expect(screen.queryByText("测试预设")).toBeNull();
    expect(screen.queryByText("开发与测试")).toBeNull();
    expect(screen.queryByText("外汇辩论流程测试团队")).toBeNull();
    expect(screen.getByText("31 个专业团队 · 7 个专业智能体")).toBeTruthy();
    expect(screen.getByText("其中 1 个项目核心团队 · 5 个项目核心智能体")).toBeTruthy();
    expect(screen.getByText("专业智能体团队")).toBeTruthy();
    expect(screen.getAllByText("商品研究团队").length).toBeGreaterThan(0);
    expect(screen.getByText("从供给与需求两个方向并行开展商品研究，由周期策略智能体综合形成投资研判。")).toBeTruthy();
    expect(screen.getByText("专业智能体")).toBeTruthy();
    expect(screen.getByText("共 7 个智能体")).toBeTruthy();
    expect(screen.queryByText("项目核心智能体")).toBeNull();
    expect(screen.queryByText("其他专业智能体")).toBeNull();
    expect(container.querySelectorAll(".agent-catalog-grid")).toHaveLength(1);
    expect(Array.from(container.querySelectorAll(".agent-catalog-card .agent-catalog-title span")).slice(0, 5).map((node) => node.textContent)).toEqual([
      "pair_bull", "pair_bear", "macro_technical", "fx_risk_officer", "debate_judge",
    ]);
    expect(screen.queryByText("智能体团队")).toBeNull();
    expect(screen.getAllByText("所属团队")).toHaveLength(7);
    expect(screen.getAllByText("主要职责")).toHaveLength(7);
    expect(screen.getAllByText("货币对多头分析师")).toHaveLength(1);
    expect(screen.getAllByText("get_fx_evidence_manifest").length).toBeGreaterThan(0);
    expect(screen.getAllByText("fx-hypothesis-falsification").length).toBeGreaterThan(0);
    expect(screen.queryByText("外汇假设证伪")).toBeNull();
    expect(screen.getAllByText("5 个智能体").length).toBeGreaterThan(0);
    expect(screen.getAllByText("输入：研究标的 · 时间周期 · 研究目标").length).toBeGreaterThan(0);
    expect(fetchMock.mock.calls.map(([input]) => String(input))).not.toContain("/swarm/presets/fx_pair_debate_desk_smoke");
    fireEvent.click(screen.getAllByRole("button", { name: "查看团队" })[0]);
    await screen.findByText("团队详情");
    expect(screen.getByText("外汇多智能体辩论团队")).toBeTruthy();
    fireEvent.click(screen.getByText("返回智能体中心"));


    fireEvent.click((await screen.findAllByRole("button", { name: /外汇多智能体辩论团队/ }))[0]);

    await screen.findByText("团队详情");
    expect(screen.getAllByText("货币对多头分析师").length).toBeGreaterThan(0);
    expect(screen.getAllByText("外汇风险官").length).toBeGreaterThan(0);
    expect(screen.getByText("第 2 阶段")).toBeTruthy();
    expect(screen.getByText("第 3 阶段")).toBeTruthy();
    expect(container.querySelectorAll(".swarm-layer-body")).toHaveLength(3);
    expect(container.querySelectorAll(".swarm-workflow-connector")).toHaveLength(2);
    expect(screen.getAllByText("主要职责").length).toBeGreaterThan(0);
    expect(screen.getAllByText("可调用工具").length).toBeGreaterThan(0);
    expect(screen.getAllByText("专业技能").length).toBeGreaterThan(0);
    expect(screen.getAllByText("未显式配置").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByText("返回智能体中心"));
    fireEvent.click(await screen.findByRole("button", { name: /商品研究团队/ }));
    await screen.findByText("团队详情");
    expect(screen.queryByText("平台内置")).toBeNull();
    expect(screen.getByText("协作流程")).toBeTruthy();
    expect(screen.getAllByText("可调用工具").length).toBeGreaterThan(0);
    expect(screen.getAllByText("专业技能").length).toBeGreaterThan(0);
  });

  it("searches Chinese display names and English preset ids", async () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/swarm/presets") return jsonResponse(presetList);
      if (path === "/swarm/presets/fx_debate_team") return jsonResponse(fxPresetDetail);
      if (path === "/swarm/presets/commodity_research_team") return jsonResponse(commodityPresetDetail);
      if (path.startsWith("/swarm/presets/professional_team_")) return jsonResponse({ name: path.slice(path.lastIndexOf("/") + 1), agents: [], tasks: [], layers: [] });
      return jsonResponse({}, 404);
    });

    render(<App />);

    const input = await screen.findByPlaceholderText("搜索团队、智能体、工具或技能");
    fireEvent.change(input, { target: { value: "商品" } });
    expect(screen.getAllByText("商品研究团队").length).toBeGreaterThan(0);

    fireEvent.change(input, { target: { value: "fx_debate_team" } });
    expect(screen.getAllByText("外汇多智能体辩论团队").length).toBeGreaterThan(0);
    fireEvent.change(input, { target: { value: "外汇与宏观" } });
    expect(screen.getAllByText("外汇多智能体辩论团队").length).toBeGreaterThan(0);


    fireEvent.change(input, { target: { value: "fx_pair_debate_desk_smoke" } });
    expect(screen.queryByText("外汇辩论流程测试团队")).toBeNull();
    expect(screen.getByText("没有匹配的智能体团队")).toBeTruthy();
  });

  it("filters the professional agent catalog by aliases, capabilities, and category", async () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/live") return jsonResponse({ status: "healthy" });
      if (path === "/swarm/presets") return jsonResponse(presetList);
      if (path === "/swarm/presets/fx_debate_team") return jsonResponse(fxPresetDetail);
      if (path === "/swarm/presets/commodity_research_team") return jsonResponse(commodityPresetDetail);
      if (path.startsWith("/swarm/presets/professional_team_")) return jsonResponse({ name: path.slice(path.lastIndexOf("/") + 1), agents: [], tasks: [], layers: [] });
      return jsonResponse({}, 404);
    });

    render(<App />);

    const agentInputs = await screen.findAllByPlaceholderText("搜索智能体、团队、工具或技能");
    const agentInput = agentInputs[agentInputs.length - 1];
    fireEvent.change(agentInput, { target: { value: "外汇与宏观" } });
    expect(screen.getByText("共 5 个智能体")).toBeTruthy();

    fireEvent.change(agentInput, { target: { value: "get_fx_evidence_manifest" } });
    expect(screen.getByText("货币对多头分析师")).toBeTruthy();

    fireEvent.change(agentInput, { target: { value: "fx-hypothesis-falsification" } });
    expect(screen.getByText("货币对空头分析师")).toBeTruthy();

    fireEvent.change(agentInput, { target: { value: "" } });
    const categoryButtons = screen.getAllByRole("button", { name: "宏观与外汇" });
    fireEvent.click(categoryButtons[categoryButtons.length - 1]);
    expect(screen.getByText("共 5 个智能体")).toBeTruthy();
  });

  it("keeps readable agent cards when one team detail fails", async () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/live") return jsonResponse({ status: "healthy" });
      if (path === "/swarm/presets") return jsonResponse(presetList);
      if (path === "/swarm/presets/fx_debate_team") return jsonResponse(fxPresetDetail);
      if (path === "/swarm/presets/commodity_research_team") return jsonResponse(commodityPresetDetail);
      if (path === "/swarm/presets/professional_team_1") return Promise.reject(new TypeError("detail unavailable"));
      if (path.startsWith("/swarm/presets/professional_team_")) return jsonResponse({ name: path.slice(path.lastIndexOf("/") + 1), agents: [], tasks: [], layers: [] });
      return jsonResponse({}, 404);
    });

    render(<App />);

    expect(await screen.findByText(/部分智能体信息暂时无法加载/)).toBeTruthy();
    expect(screen.getByText("专业智能体")).toBeTruthy();
    expect(screen.getByText("共 7 个智能体")).toBeTruthy();
    expect(screen.getAllByText("货币对多头分析师")).toHaveLength(1);
  });

  it("falls back gracefully for unknown preset localization", () => {
    expect(presetDisplay({ name: "custom_unknown_team", title: "Custom Unknown", description: "", source: "user" })).toMatchObject({
      title: "Custom Unknown",
      badge: "本地自定义",
    });
  });

  it("surfaces preset list loading errors", async () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/swarm/presets") return jsonResponse({ detail: "preset metadata unavailable" }, 500);
      return jsonResponse({}, 404);
    });

    render(<App />);

    await screen.findByText("preset metadata unavailable");
  });

  it("shows an empty state when no presets are returned", async () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/swarm/presets") return jsonResponse([]);
      return jsonResponse({}, 404);
    });

    render(<App />);

    await waitFor(() => expect(screen.getByText("当前后端没有返回任何预设。")).toBeTruthy());
  });
});
