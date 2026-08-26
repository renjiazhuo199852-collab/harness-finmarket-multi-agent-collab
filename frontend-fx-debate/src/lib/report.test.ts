import { describe, expect, it } from "vitest";
import { buildReportDownloadHtml, buildReportDownloadMarkdown, displayReportMarkdown, localizeReportMarkdown, markdownToHtml, sanitizeConversationReply, sanitizeReportDisplayText } from "@/lib/report";

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

  it("builds a standalone HTML report with readable tables and code blocks", () => {
    const html = buildReportDownloadHtml(
      { target: "EURUSD", markdown: "# FinalDecision\n\n| Field | Value |\n|---|---|\n| action | wait |\n\n```json\n{\"decision\":\"wait\"}\n```" },
      [{ role: "风险分析师", report: "## Risk review\n\n风险已复核" }],
    );

    expect(html).toContain("<!doctype html>");
    expect(html).toContain("<table>");
    expect(html).toContain("<pre><code>{&quot;decision&quot;:&quot;wait&quot;}</code></pre>");
    expect(html).toContain("风险分析师");
    expect(html).toContain("最终决策");
  });

  it("escapes unsafe HTML while preserving report formatting", () => {
    const html = markdownToHtml("# 标题\n\n<script>alert(1)</script>\n\n- 项目");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("<ul>");
    expect(html).not.toContain("<script>alert");
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

  it("does not show confidence in visible report text or conversation text", () => {
    const report = sanitizeReportDisplayText("- 决策：做空\n- 置信度：35%\n核心判断：宏观偏空。\n方向置信度较低。\n");
    const reply = sanitizeConversationReply("最终方向：做空。置信度较低，confidence: 35%。");

    expect(report).toContain("决策：做空");
    expect(report).toContain("核心判断：宏观偏空");
    expect(report).not.toContain("置信度");
    expect(reply).toBe("最终方向：做空。");
    expect(reply).not.toContain("confidence");
    expect(reply).not.toContain("置信度");
  });

  it("hides internal evidence lookup diagnostics from the conversation reply", () => {
    const displayed = sanitizeConversationReply(
      "EURUSD 回测结论为做空。Evidence Context 后端索引异常，无法二次回查。请查看最终报告。",
    );

    expect(displayed).toContain("EURUSD 回测结论为做空");
    expect(displayed).toContain("请查看最终报告");
    expect(displayed).not.toContain("Evidence Context");
    expect(displayed).not.toContain("二次回查");
  });
});
