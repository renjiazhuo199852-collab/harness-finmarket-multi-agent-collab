import { render, screen } from "@testing-library/react";
import { MarkdownContent } from "@/components/MarkdownContent";

describe("MarkdownContent", () => {
  it("renders GFM tables and common report blocks as semantic elements", () => {
    const markdown = [
      "## 交易计划",
      "",
      "| 情景 | 入场 | 止损 |",
      "| --- | ---: | ---: |",
      "| 反弹做空 | 1.1090 | 1.1165 |",
      "",
      "- [x] 等待 4H 收盘",
      "- 风险：事件前减仓",
      "",
      "`EURUSD` 与 https://example.com",
    ].join("\n");
    render(<MarkdownContent>{markdown}</MarkdownContent>);

    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "情景" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "1.1090" })).toBeTruthy();
    expect(screen.getByRole("list")).toBeTruthy();
    expect(screen.getByRole("checkbox")).toBeTruthy();
    expect(screen.getByText("EURUSD")).toBeTruthy();
    expect(screen.getByRole("link", { name: "https://example.com" }).getAttribute("href")).toBe("https://example.com");
  });
});
