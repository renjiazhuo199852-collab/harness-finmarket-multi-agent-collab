import type { AgentReport, FxReport } from "@/types";

type DownloadReport = Partial<FxReport> & { target?: string };

const TRANSLATIONS: Array<[string, string]> = [
  ["FinalDecision", "最终决策"],
  ["Decision rationale (Debate Judge)", "决策依据（辩论裁决）"],
  ["State summary (1D / 4H / Macro / News)", "状态摘要（1D / 4H / 宏观 / 新闻）"],
  ["News / event_state", "新闻 / 事件状态"],
  ["Adopted / rejected claim_ids", "采用 / 拒绝的结论编号"],
  ["Key evidence_ids", "关键证据编号"],
  ["Risk envelope", "风险边界"],
  ["Notes on tool audit", "工具审计说明"],
  ["Decision rationale", "决策依据"],
  ["data-quality envelope", "数据质量边界"],
  ["cross-timeframe discontinuity", "跨周期不连续"],
  ["observation_lag", "观测延迟"],
  ["event_calendar", "事件日历"],
  ["relative spread", "相对利差"],
  ["forward policy-rate path", "前瞻政策利率路径"],
  ["directional trigger", "方向性触发条件"],
  ["no event-anchored catalyst can be added", "无法加入由事件锚定的催化因素"],
  ["The PM-arbiter process is to adopt V2-registered claims that are evidence-supported and not in conflict with the data-quality envelope.", "组合经理裁决流程只采用 V2 中登记、且有证据支持并且不违反数据质量边界的结论。"],
  ["Of the three upstream V2 packs:", "三份上游 V2 分析包的情况如下："],
  ["The net directional bias from adopted claims leans USD-supportive / EUR-weak, but:", "已采用结论的总体方向偏向美元走强 / 欧元走弱，但存在以下限制："],
  ["The conservative, contract-compliant conclusion is", "符合契约的保守结论是"],
  ["One get_fx_evidence_by_ids call re-fetched", "通过一次 get_fx_evidence_by_ids 调用重新获取了"],
  ["No orders are placed.", "本次不下单。"],
  ["primary", "主周期"],
  ["tactical", "战术周期"],
  ["directional", "方向性"],
  ["non-continuous", "不连续"],
  ["non-actionable", "不可执行"],
  ["realized vol", "实现波动率"],
  ["annualized", "年化"],
  ["oversold-extreme tail signal", "超卖极值尾部信号"],
  ["reversal confirmation", "反转确认"],
  ["story clusters", "新闻事件簇"],
  ["monitoring/observation-type", "监测/观察类"],
  ["neutral direction", "中性方向"],
  ["supportable", "可支持"],
  ["narrative", "叙事"],
  ["slow-frequency state", "慢频状态"],
  ["fresh", "最新"],
  ["close", "收盘价"],
  ["return", "收益"],
  ["spread", "利差"],
  ["catalyst", "催化因素"],
  ["Evidence", "证据"],
  ["Macro", "宏观"],
  ["Technical", "技术"],
  ["News", "新闻"],
  ["Risk", "风险"],
  ["Decision", "决策"],
  ["State", "状态"],
  ["summary", "摘要"],
  ["Machine-readable V2", "机器可读 V2"],
  ["Tool audit trail", "工具调用审计"],
  ["Observed Fact", "观察事实"],
  ["Expected Effect", "预期影响"],
  ["Cross-confirmation", "跨周期确认"],
  ["Risk review", "风险复核"],
  ["Audit", "审计信息"],
  ["Field", "字段"],
  ["Value", "值"],
  ["Summary", "摘要"],
  ["Status", "状态"],
  ["reliability", "可靠性"],
  ["analysis_status", "分析状态"],
  ["evidence_context_id", "证据上下文编号"],
  ["canonical_symbol", "标准货币对"],
  ["display_symbol", "展示货币对"],
  ["requested_symbol", "请求货币对"],
  ["direction_semantics", "方向语义"],
  ["risk_profile", "风险偏好"],
  ["data_as_of", "数据截止时间"],
  ["horizon_days", "期限天数"],
  ["scenario_probabilities", "情景概率"],
  ["trade_plan", "交易方案"],
  ["entry_zone", "入场区间"],
  ["stop_loss", "止损"],
  ["take_profit", "止盈"],
  ["invalidation_conditions", "失效条件"],
  ["missing_data", "缺失数据"],
  ["next_review_trigger", "下一次复核触发条件"],
  ["key_evidence_ids", "关键证据编号"],
  ["adopted_claim_ids", "采用结论编号"],
  ["rejected_claim_ids", "拒绝结论编号"],
  ["technical_state", "技术状态"],
  ["event_state", "事件状态"],
  ["quote_supported", "报价支持"],
  ["non-actionable", "不可执行"],
  ["indeterminate", "无法确定"],
  ["diverging", "分化"],
  ["balanced", "均衡"],
  ["partial", "部分可用"],
  ["complete", "完整"],
  ["unknown", "未知"],
  ["bullish", "看涨"],
  ["bearish", "看跌"],
  ["neutral", "震荡"],
  ["stale", "过期"],
  ["wait", "等待确认"],
  ["hedge", "对冲"],
  ["approved", "已通过"],
  ["rejected", "已拒绝"],
];

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function localizeText(value: string): string {
  return [...TRANSLATIONS]
    .sort((a, b) => b[0].length - a[0].length)
    .reduce((text, [source, target]) => {
      const expression = new RegExp(`\\b${escapeRegExp(source)}\\b`, "g");
      return text.replace(expression, target);
    }, value);
}

/** Translate report prose while leaving fenced and inline code machine-readable. */
export function localizeReportMarkdown(markdown: string): string {
  return markdown
    .split(/(```[\s\S]*?```|`[^`\n]+`)/g)
    .map((part, index) => index % 2 === 1 ? part : localizeText(part))
    .join("");
}

/** Keep the full report visible while using neutral wording for evidence-state notes. */
export function sanitizeReportDisplayText(text: string): string {
  return [
    [/当前证据不足以形成交易信号/g, "当前结论作为背景判断，不形成交易信号"],
    [/数据不足/g, "数据有限"],
    [/证据不足/g, "证据有限"],
    [/数据不完整/g, "数据有限"],
    [/样本不足/g, "样本有限"],
    [/无法确认/g, "未形成确认"],
    [/无法判断/g, "未形成判断"],
    [/无法确定/g, "未形成方向结论"],
    [/不可判定/g, "未形成方向结论"],
    [/不能判断/g, "不形成判断"],
    [/不能转化为交易信号/g, "不形成交易信号"],
    [/缺少/g, "待补充"],
    [/缺失/g, "未提供"],
    [/不足/g, "有限"],
    [/无法/g, "未形成"],
    [/不能/g, "不形成"],
  ].reduce((value, [pattern, replacement]) => value.replace(pattern as RegExp, replacement as string), text);
}

/** Remove machine-readable sections from the report rendered in the UI. */
export function displayReportMarkdown(markdown: string): string {
  const output: string[] = [];
  let inFence = false;
  let jsonLines: string[] | null = null;
  const flushJson = (): void => {
    if (jsonLines) output.push(...jsonLines);
    jsonLines = null;
  };

  for (const line of markdown.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (inFence) {
      if (trimmed.startsWith("```")) inFence = false;
      continue;
    }
    if (trimmed.startsWith("```")) {
      inFence = true;
      continue;
    }
    if (/^#{1,6}\s*(?:\d+[.)]\s*)?(?:机器可读|machine[- ]readable|riskreview)/i.test(trimmed)) continue;
    if (jsonLines) {
      const pendingJson = jsonLines;
      pendingJson.push(line);
      try {
        JSON.parse(pendingJson.join("\n"));
        jsonLines = null;
      } catch {
        if (pendingJson.length >= 200) flushJson();
      }
      continue;
    }
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        JSON.parse(trimmed);
        continue;
      } catch {
        jsonLines = [line];
        continue;
      }
    }
    output.push(line);
  }
  flushJson();
  return output.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

export function buildReportDownloadMarkdown(
  report: DownloadReport,
  agentReports: Array<Pick<AgentReport, "role" | "report">>,
  target?: string,
): string {
  const reportTarget = target || report.target || "当前货币对";
  const finalReport = report.markdown ? localizeReportMarkdown(report.markdown) : "（本次运行未返回裁决 Markdown。）";
  const sections = [
    "# 外汇辩论研究报告",
    `> 研究对象：${reportTarget}`,
    "",
    "## 辩论裁决最终结果",
    finalReport,
  ];
  agentReports.forEach((item) => {
    sections.push("", `## ${item.role || "研究节点"}`, localizeReportMarkdown(item.report));
  });
  return `${sections.join("\n\n")}\n`;
}

export function downloadTextFile(content: string, filename: string): void {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
