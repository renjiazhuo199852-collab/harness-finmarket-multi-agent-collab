import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { AlertCircle, CheckCircle2, Clock3, FileDiff, RefreshCw, RotateCcw, Save, Sparkles, X } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import type { AgentEditHistoryEntry, AgentEditProposal, AgentEditorPayload, AgentReviewCheck, AgentReviewFinding, SwarmPresetAgent } from "@/types";

type Notice = { kind: "success" | "error" | "info"; text: string } | null;

export interface AgentEditorPanelProps {
  presetName: string;
  agent: SwarmPresetAgent;
  displayName: string;
  onClose: () => void;
  onChanged: () => void;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return "配置已被其他页面修改，请先刷新后再生成方案。";
  return error instanceof Error ? error.message : "请求失败，请检查后端服务。";
}

function revisionLabel(value: string): string {
  return value ? value.slice(0, 12) : "未生成";
}

function skillChangeList(items: string[] | undefined, empty: string): ReactElement {
  return items?.length ? <div className="agent-editor-chip-list">{items.map((item) => <span key={item}>{item}</span>)}</div> : <span className="agent-editor-empty">{empty}</span>;
}

function reviewItemText(item: string | AgentReviewFinding | AgentReviewCheck): string {
  if (typeof item === "string") return item;
  const value = item as Record<string, unknown>;
  const label = typeof value.type === "string" ? value.type : typeof value.name === "string" ? value.name : "审查项";
  const detail = typeof value.message === "string"
    ? value.message
    : typeof value.description === "string"
      ? value.description
      : typeof value.result === "string"
        ? value.result
        : "";
  const status = typeof value.passed === "boolean" ? value.passed ? "通过" : "未通过" : "";
  return [label, detail, status].filter(Boolean).join("：") || JSON.stringify(value);
}

export function AgentEditorPanel({ presetName, agent, displayName, onClose, onChanged }: AgentEditorPanelProps): ReactElement {
  const [editor, setEditor] = useState<AgentEditorPayload | null>(null);
  const [proposal, setProposal] = useState<AgentEditProposal | null>(null);
  const [history, setHistory] = useState<AgentEditHistoryEntry[]>([]);
  const [instruction, setInstruction] = useState("");
  const [sessionId] = useState(() => `agent-edit-session-${Math.random().toString(36).slice(2)}`);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [proposalDirty, setProposalDirty] = useState(false);
  const [candidateError, setCandidateError] = useState("");
  const [skillOverridesText, setSkillOverridesText] = useState("{}");

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const loadEditor = async (message?: string) => {
    setLoading(true);
    try {
      const next = await api.getAgentEditor(presetName, agent.id);
      setEditor(next);
      setProposal(null);
      setProposalDirty(false);
      setCandidateError("");
      setSkillOverridesText("{}");
      if (message) setNotice({ kind: "success", text: message });
      else setNotice(null);
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadEditor();
    // The selected agent/preset is the lifetime of this panel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetName, agent.id]);

  const generateProposal = async () => {
    if (!editor || !instruction.trim()) return;
    setWorking(true);
    setNotice(null);
    try {
      const next = await api.proposeAgentEdit(presetName, agent.id, {
        instruction: instruction.trim(),
        base_revision: editor.revision,
        session_id: sessionId,
      });
      setProposal(next);
      setProposalDirty(false);
      setCandidateError("");
      setSkillOverridesText(JSON.stringify(next.candidate.skill_overrides, null, 2));
      setNotice({ kind: next.review.approved ? "success" : "error", text: next.review.approved ? "修改方案已通过模型和规则审核，等待确认应用。" : "修改方案未通过审核，请继续调整修改方向。" });
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    } finally {
      setWorking(false);
    }
  };

  const reviseProposal = async () => {
    if (!editor || !proposal || !proposalDirty || candidateError) return;
    setWorking(true);
    setNotice(null);
    try {
      const next = await api.reviseAgentEdit(presetName, agent.id, proposal.proposal_id, {
        base_revision: editor.revision,
        candidate: proposal.candidate,
      });
      setProposal(next);
      setProposalDirty(false);
      setSkillOverridesText(JSON.stringify(next.candidate.skill_overrides, null, 2));
      setNotice({ kind: next.review.approved ? "success" : "error", text: next.review.approved ? "人工修改已通过中文审核，可以应用。" : "人工修改未通过审核，请继续调整。" });
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    } finally {
      setWorking(false);
    }
  };

  const applyProposal = async () => {
    if (!editor || !proposal || !proposal.review.approved) return;
    setWorking(true);
    try {
      const next = await api.applyAgentEdit(presetName, agent.id, proposal.proposal_id, editor.revision);
      setEditor(next);
      setProposal(null);
      setProposalDirty(false);
      setCandidateError("");
      setSkillOverridesText("{}");
      setInstruction("");
      setNotice({ kind: "success", text: "配置已应用并刷新；只影响新启动的运行，当前运行保持原配置。" });
      onChanged();
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    } finally {
      setWorking(false);
    }
  };

  const resetAgent = async () => {
    if (!editor || !window.confirm("恢复默认后将移除该 agent 的用户覆盖配置，历史记录仍会保留。继续吗？")) return;
    setWorking(true);
    try {
      const next = await api.resetAgentEdit(presetName, agent.id, editor.revision);
      setEditor(next);
      setProposal(null);
      setProposalDirty(false);
      setCandidateError("");
      setSkillOverridesText("{}");
      setInstruction("");
      setNotice({ kind: "success", text: "已恢复默认配置。" });
      onChanged();
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    } finally {
      setWorking(false);
    }
  };

  const reloadAgent = async () => {
    setWorking(true);
    try {
      await api.reloadPreset(presetName);
      await loadEditor("配置已重新加载；只影响新启动的运行。");
      onChanged();
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    } finally {
      setWorking(false);
    }
  };

  const loadHistory = async () => {
    setShowHistory((current) => !current);
    if (history.length > 0) return;
    try {
      const result = await api.getAgentEditHistory(presetName, agent.id);
      setHistory(result.entries);
    } catch (error) {
      setNotice({ kind: "error", text: errorText(error) });
    }
  };

  return <div className="agent-editor-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="agent-editor-panel" role="dialog" aria-modal="true" aria-label="编辑智能体配置" onMouseDown={(event) => event.stopPropagation()}>
    <div className="agent-editor-header"><div><span className="eyebrow">AGENT CONFIGURATION</span><h3>{displayName}</h3><code>{presetName} / {agent.id}</code></div><button className="icon-button" title="关闭编辑器" onClick={onClose}><X size={16} /></button></div>
    {loading ? <div className="agent-editor-loading">正在读取当前配置...</div> : !editor ? <div className="agent-editor-error"><AlertCircle size={16} />无法读取 agent 配置</div> : <>
      <div className="agent-editor-meta"><span>配置来源<strong>{editor.source === "user_override" ? "用户覆盖" : "默认配置"}</strong></span><span>版本<strong>{revisionLabel(editor.revision)}</strong></span><span>运行范围<strong>仅新运行</strong></span></div>
      {notice ? <div className={`agent-editor-notice agent-editor-notice-${notice.kind}`}>{notice.kind === "success" ? <CheckCircle2 size={15} /> : notice.kind === "error" ? <AlertCircle size={15} /> : <Clock3 size={15} />}<span>{notice.text}</span></div> : null}
      <section className="agent-editor-section"><div className="agent-editor-section-head"><h4>当前生效配置</h4><span>只读</span></div><label>系统 prompt<pre className="agent-editor-prompt">{editor.effective.system_prompt || "未配置"}</pre></label><div><span className="agent-editor-label">技能绑定</span>{skillChangeList(editor.effective.skills, "未配置技能")}</div>{Object.keys(editor.effective.skill_overrides).length > 0 ? <div><span className="agent-editor-label">专属技能覆盖</span>{skillChangeList(Object.keys(editor.effective.skill_overrides), "无")}</div> : null}<div className="agent-editor-skill-content"><span className="agent-editor-label">技能内容</span>{Object.entries(editor.effective_skill_contents || {}).map(([name, content]) => <details key={name}><summary>{name}</summary><pre className="agent-editor-prompt">{content}</pre></details>)}</div></section>
      <section className="agent-editor-section"><div className="agent-editor-section-head"><h4>提出修改方向</h4><span>模型生成方案</span></div><textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="例如：增加数据时效审查，强化宏观与技术冲突时的风险提示。" rows={4} /><button className="primary-button" disabled={working || !instruction.trim()} onClick={() => void generateProposal()}><Sparkles size={15} />{working ? "处理中..." : "生成修改方案"}</button></section>
      {proposal ? <section className="agent-editor-section agent-editor-proposal"><div className="agent-editor-section-head"><h4><FileDiff size={15} />修改方案</h4><span className={proposalDirty ? "agent-editor-pending" : proposal.review.approved ? "agent-editor-approved" : "agent-editor-rejected"}>{proposalDirty ? "待重新审核" : proposal.review.approved ? "审核通过" : "需要调整"}</span></div><div className="agent-editor-manual-note">可以直接修改下面的候选内容。手动修改后必须重新审核，审核通过后才能应用。</div><label>候选系统 prompt<textarea className="agent-editor-editable" rows={9} value={proposal.candidate.system_prompt} onChange={(event) => { setProposal((current) => current ? { ...current, candidate: { ...current.candidate, system_prompt: event.target.value } } : current); setProposalDirty(true); }} /></label><label>候选技能绑定<textarea className="agent-editor-editable" rows={3} value={proposal.candidate.skills.join("\n")} onChange={(event) => { const skills = event.target.value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean); setProposal((current) => current ? { ...current, candidate: { ...current.candidate, skills, skill_overrides: Object.fromEntries(Object.entries(current.candidate.skill_overrides).filter(([name]) => skills.includes(name))) } } : current); setProposalDirty(true); }} /><span className="agent-editor-field-help">每行一个技能名称，只能使用系统提供的技能。</span></label><label>专属技能覆盖（JSON）<textarea className="agent-editor-editable agent-editor-json-editable" rows={5} value={skillOverridesText} onChange={(event) => { const text = event.target.value; setSkillOverridesText(text); try { const parsed = JSON.parse(text) as Record<string, unknown>; if (!parsed || Array.isArray(parsed) || Object.values(parsed).some((value) => typeof value !== "string")) throw new Error(); setProposal((current) => current ? { ...current, candidate: { ...current.candidate, skill_overrides: parsed as Record<string, string> } } : current); setCandidateError(""); } catch { setCandidateError("技能覆盖必须是 value 为字符串的 JSON 对象"); } setProposalDirty(true); }} /><span className="agent-editor-field-help">没有专属技能内容时填写 {}。</span></label>{candidateError ? <div className="agent-editor-notice agent-editor-notice-error"><AlertCircle size={15} />{candidateError}</div> : null}<div className="agent-editor-review"><strong>审核结果：{proposalDirty ? "修改后尚未重新审核" : `风险等级：${proposal.review.risk_level}`}</strong>{!proposalDirty && (proposal.review.findings.length ? <ul>{proposal.review.findings.map((item, index) => <li key={`${reviewItemText(item)}-${index}`}>{reviewItemText(item)}</li>)}</ul> : <span>未发现阻断问题</span>)}{!proposalDirty && proposal.review.checks.length ? <><strong>校验项</strong><ul>{proposal.review.checks.map((item, index) => <li key={`${reviewItemText(item)}-${index}`}>{reviewItemText(item)}</li>)}</ul></> : null}</div><label>Prompt 差异（最近一次审核）<pre className="agent-editor-diff">{proposal.diff.prompt || "无 prompt 变化"}</pre></label><div className="agent-editor-change-grid"><div><span>新增技能</span>{skillChangeList(proposal.diff.skills_added, "无")}</div><div><span>移除技能</span>{skillChangeList(proposal.diff.skills_removed, "无")}</div><div><span>修改技能</span>{skillChangeList(proposal.diff.skills_modified, "无")}</div></div><div className="agent-editor-actions"><button className="secondary-button" onClick={() => setProposal(null)}>继续修改</button>{proposalDirty ? <button className="primary-button" disabled={working || Boolean(candidateError)} onClick={() => void reviseProposal()}><CheckCircle2 size={15} />重新审核</button> : <button className="primary-button" disabled={working || !proposal.review.approved} onClick={() => void applyProposal()}><Save size={15} />应用修改</button>}</div></section> : null}
      <div className="agent-editor-footer-actions"><button className="secondary-button" disabled={working} onClick={() => void reloadAgent()}><RefreshCw size={14} />刷新配置</button><button className="secondary-button" disabled={working || editor.source !== "user_override"} onClick={() => void resetAgent()}><RotateCcw size={14} />恢复默认</button><button className="text-button" onClick={() => void loadHistory()}>查看版本历史</button></div>
      {showHistory ? <section className="agent-editor-history"><h4>版本历史</h4>{history.length ? <div>{history.slice().reverse().map((entry, index) => <div className="agent-editor-history-row" key={`${entry.revision || entry.previous_revision || "entry"}-${index}`}><strong>{entry.action === "reset" ? "恢复默认" : "应用修改"}</strong><code>{revisionLabel(entry.revision || entry.previous_revision || "")}</code><span>{entry.instruction || ""}</span></div>)}</div> : <span className="agent-editor-empty">暂无已应用版本</span>}</section> : null}
    </>}
    </section>
  </div>;
}
