import { describe, expect, it } from "vitest";
import { isRunActive, settleCancellation } from "@/lib/run_controls";

describe("run cancellation controls", () => {
  it("keeps a hydrated running run active after a page refresh", () => {
    expect(isRunActive(false, "running")).toBe(true);
    expect(isRunActive(false, "pending")).toBe(true);
    expect(isRunActive(false, "completed")).toBe(false);
  });

  it("re-enables sending after the server accepts cancellation", () => {
    expect(settleCancellation({ busy: true, cancelling: true }, "cancelled")).toEqual({
      busy: false,
      cancelling: false,
    });
  });

  it("also recovers when the server reports that no loop remains", () => {
    expect(settleCancellation({ busy: true, cancelling: true }, "no_active_loop")).toEqual({
      busy: false,
      cancelling: false,
    });
  });
});
