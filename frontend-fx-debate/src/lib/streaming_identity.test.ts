import { describe, expect, it } from "vitest";

import { nextStreamingIdentity, streamingIdentityCopy } from "@/lib/streaming_identity";
import type { SessionEvent } from "@/types";

function event(type: string, data: Record<string, unknown> = {}): SessionEvent {
  return { type, data };
}

describe("streaming assistant identity", () => {
  it("uses the research assistant label for ordinary streaming output", () => {
    const identity = nextStreamingIdentity("research-assistant", event("text_delta", { delta: "分析中" }));

    expect(streamingIdentityCopy(identity)).toEqual({
      name: "研究助手",
      status: "● 正在生成",
    });
  });

  it("does not show FX Debate for another swarm preset", () => {
    const identity = nextStreamingIdentity("research-assistant", event("swarm.started", {
      preset: "market_research_team",
      run_id: "run-generic",
    }));

    expect(streamingIdentityCopy(identity)).toEqual({
      name: "研究助手",
      status: "● 正在生成",
    });
  });

  it("shows FX Debate only after fx_debate_team starts", () => {
    const identity = nextStreamingIdentity("research-assistant", event("swarm.started", {
      preset: "fx_debate_team",
      run_id: "run-fx",
    }));

    expect(streamingIdentityCopy(identity)).toEqual({
      name: "FX Debate",
      status: "● 实时",
    });
  });
});
