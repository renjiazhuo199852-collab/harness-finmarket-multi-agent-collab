import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentEditorPanel } from "@/components/AgentEditorPanel";
import { clearApiConfig } from "@/lib/api_config";
import type { SwarmPresetAgent } from "@/types";

const agent: SwarmPresetAgent = {
  id: "pair_bull",
  role: "Pair Bull",
  tools: ["load_skill"],
  skills: ["fx-hypothesis-falsification"],
};

const editor = {
  preset_name: "fx_debate_team",
  agent_id: "pair_bull",
  role: "Pair Bull",
  source: "default",
  revision: "base-revision",
  updated_at: null,
  effective: { system_prompt: "Current prompt", skills: ["fx-hypothesis-falsification"], skill_overrides: {} },
  defaults: { system_prompt: "Current prompt", skills: ["fx-hypothesis-falsification"], skill_overrides: {} },
  effective_skill_contents: { "fx-hypothesis-falsification": "<skill name=\"fx-hypothesis-falsification\">Audit skill</skill>" },
  default_skill_contents: { "fx-hypothesis-falsification": "<skill name=\"fx-hypothesis-falsification\">Audit skill</skill>" },
  available_skills: [{ name: "fx-hypothesis-falsification", description: "Audit hypotheses", category: "analysis" }],
};

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, text: () => Promise.resolve(JSON.stringify(body)) } as Response;
}

describe("AgentEditorPanel", () => {
  beforeEach(() => {
    clearApiConfig();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    clearApiConfig();
  });

  it("keeps rejected proposals unapplied and applies an approved proposal", async () => {
    let proposalCount = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/editor")) return jsonResponse(editor);
      if (path.endsWith("/proposals")) {
        proposalCount += 1;
        return jsonResponse({
          proposal_id: `proposal-${proposalCount}`,
          preset_name: "fx_debate_team",
          agent_id: "pair_bull",
          instruction: "强化审查",
          base_revision: "base-revision",
          candidate: { system_prompt: "Candidate prompt", skills: ["fx-hypothesis-falsification"], skill_overrides: {} },
          diff: { prompt: "-Current prompt\n+Candidate prompt", skills_added: [], skills_removed: [], skills_modified: [] },
          review: proposalCount === 1
            ? { approved: false, risk_level: "high", findings: [{ type: "role_intent_drift", message: "需要补充边界" }], checks: [] }
            : { approved: true, risk_level: "low", findings: [], checks: [{ name: "safety_rule", passed: true, message: "安全规则保留" }] },
          created_at: "2026-08-25T00:00:00Z",
          session_id: "session",
        });
      }
      if (path.endsWith("/apply")) return jsonResponse({ ...editor, source: "user_override", revision: "new-revision", updated_at: "2026-08-25T00:00:00Z" });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AgentEditorPanel presetName="fx_debate_team" agent={agent} displayName="货币对多头分析师" onClose={vi.fn()} onChanged={vi.fn()} />);
    await screen.findByText("当前生效配置");
    const input = screen.getByPlaceholderText(/增加数据时效审查/);
    fireEvent.change(input, { target: { value: "强化数据审查" } });
    fireEvent.click(screen.getByRole("button", { name: "生成修改方案" }));
    await screen.findByText("需要调整");
    expect((screen.getByRole("button", { name: "应用修改" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "继续修改" }));
    fireEvent.change(input, { target: { value: "强化数据时效和事件风险审查" } });
    fireEvent.click(screen.getByRole("button", { name: "生成修改方案" }));
    await screen.findByText("审核通过");
    await screen.findByText("safety_rule：安全规则保留：通过");
    fireEvent.click(screen.getByRole("button", { name: "应用修改" }));
    await waitFor(() => expect(screen.getByText("配置已应用并刷新；只影响新启动的运行，当前运行保持原配置。")).toBeTruthy());
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/apply"))).toBe(true);
  });

  it("surfaces a revision conflict when generating from stale configuration", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/editor")) return jsonResponse(editor);
      if (path.endsWith("/proposals")) return jsonResponse({ detail: "agent configuration changed" }, 409);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AgentEditorPanel presetName="fx_debate_team" agent={agent} displayName="货币对多头分析师" onClose={vi.fn()} onChanged={vi.fn()} />);
    await screen.findByText("当前生效配置");
    fireEvent.change(screen.getByPlaceholderText(/增加数据时效审查/), { target: { value: "强化审查" } });
    fireEvent.click(screen.getByRole("button", { name: "生成修改方案" }));
    await screen.findByText("配置已被其他页面修改，请先刷新后再生成方案。");
  });
});
