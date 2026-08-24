import { describe, expect, it } from "vitest";
import { buildReportDownloadMarkdown, displayReportMarkdown, localizeReportMarkdown, sanitizeReportDisplayText } from "@/lib/report";

describe("report presentation helpers", () => {
  it("localizes report headings and statuses without changing identifiers", () => {
    const localized = localizeReportMarkdown(
      "# FinalDecision — EURUSD\n\n## Audit\n\n| Field | Value |\n|---|---|\n| reliability | stale |\n\n```json\n{\"decision\":\"wait\"}\n```",
    );

    expect(localized).toContain("最终决策");
    expect(localized).toContain("审计信息");
    expect(localized).toContain("可靠性");
    expect(localized).toContain("过期");
    expect(localized).toContain("EURUSD");
    expect(localized).toContain('{"decision":"wait"}');
    expect(localized).not.toContain("FinalDecision");
  });

  it("builds a complete Chinese download document", () => {
    const markdown = buildReportDownloadMarkdown(
      { target: "EURUSD", markdown: "# FinalDecision\n\n## Audit\n\n方向：wait\n\n```json\n{\"decision\":\"wait\"}\n```" },
      [
        { role: "多头观点分析师", report: "# Observed Fact\n\n看涨证据" },
        { role: "外汇风险分析师", report: "# Risk review\n\n风险已复核" },
      ],
    );

    expect(markdown).toContain("# 外汇辩论研究报告");
    expect(markdown).toContain("## 辩论裁决最终结果");
    expect(markdown).toContain("## 多头观点分析师");
    expect(markdown).toContain("## 外汇风险分析师");
    expect(markdown).toContain("最终决策");
    expect(markdown).toContain('"decision"');
    expect(markdown).not.toContain("FinalDecision");
  });

  it("removes machine-readable JSON from the user-facing report only", () => {
    const displayed = displayReportMarkdown(
      "# 辩论裁决\n\n结论：等待确认。\n\n## 10. 机器可读 V2\n\n```json\n{\"decision\":\"wait\",\"confidence\":0.25}\n```\n\n{\"ok\":false,\"error\":{\"code\":\"FX_BUNDLE_ERROR\"}}",
    );

    expect(displayed).toContain("结论：等待确认");
    expect(displayed).not.toContain("机器可读 V2");
    expect(displayed).not.toContain('"decision"');
    expect(displayed).not.toContain("FX_BUNDLE_ERROR");
  });

  it("keeps report logic while neutralizing evidence-state wording for the page", () => {
    const displayed = sanitizeReportDisplayText("证据不足，无法判断方向；缺少价格确认，但宏观判断偏空。");

    expect(displayed).toContain("证据有限");
    expect(displayed).toContain("未形成判断");
    expect(displayed).toContain("待补充价格确认");
    expect(displayed).toContain("宏观判断偏空");
    expect(displayed).not.toContain("证据不足");
    expect(displayed).not.toContain("无法判断");
  });
});
