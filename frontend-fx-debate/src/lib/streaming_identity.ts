import type { SessionEvent } from "@/types";

export type StreamingIdentity = "research-assistant" | "fx-debate";

const STREAMING_IDENTITY_COPY: Record<StreamingIdentity, { name: string; status: string }> = {
  "research-assistant": {
    name: "研究助手",
    status: "● 正在生成",
  },
  "fx-debate": {
    name: "FX Debate",
    status: "● 实时",
  },
};

export function nextStreamingIdentity(current: StreamingIdentity, event: SessionEvent): StreamingIdentity {
  if (event.type === "swarm.started" && event.data.preset === "fx_debate_team") {
    return "fx-debate";
  }
  return current;
}

export function streamingIdentityCopy(identity: StreamingIdentity): { name: string; status: string } {
  return STREAMING_IDENTITY_COPY[identity];
}
