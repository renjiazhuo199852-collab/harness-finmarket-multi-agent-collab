import type { SessionEvent } from "@/types";

export type SSEStatus = "disconnected" | "connecting" | "connected" | "reconnecting";
export type EventHandler = (event: SessionEvent) => void;

const KNOWN_EVENTS = [
  "message.received", "attempt.created", "attempt.started", "attempt.completed", "attempt.failed",
  "text_delta", "reasoning_delta", "thinking_done", "stream_reset", "tool_call", "tool_result",
  "tool_progress", "tool_heartbeat", "llm_usage", "swarm.started", "swarm.event",
  "data_service.query_started", "data_service.stage", "data_service.query_completed",
  "data_service.query_failed", "fx_debate.context_ready", "heartbeat", "done",
];

export class SessionTransport {
  private source: EventSource | null = null;
  private retryTimer: number | undefined;
  private closed = true;
  private retryCount = 0;
  private lastEventId = "";
  private readonly seen = new Set<string>();
  private readonly order: string[] = [];
  private url = "";
  private handler: EventHandler = () => undefined;
  private statusHandler: (status: SSEStatus) => void = () => undefined;

  constructor(private readonly maxRetries = 8) {}

  connect(url: string, handler: EventHandler, statusHandler?: (status: SSEStatus) => void): void {
    this.disconnect();
    this.url = url;
    this.handler = handler;
    this.statusHandler = statusHandler || (() => undefined);
    this.closed = false;
    this.retryCount = 0;
    this.lastEventId = "";
    this.seen.clear();
    this.order.length = 0;
    this.open();
  }

  disconnect(): void {
    this.closed = true;
    this.source?.close();
    this.source = null;
    if (this.retryTimer !== undefined) window.clearTimeout(this.retryTimer);
    this.retryTimer = undefined;
    this.statusHandler("disconnected");
  }

  private open(): void {
    if (this.closed) return;
    const url = this.lastEventId
      ? `${this.url}${this.url.includes("?") ? "&" : "?"}Last-Event-ID=${encodeURIComponent(this.lastEventId)}`
      : this.url;
    this.statusHandler(this.retryCount ? "reconnecting" : "connecting");
    const source = new EventSource(url);
    this.source = source;
    source.onopen = () => {
      this.retryCount = 0;
      this.statusHandler("connected");
    };
    const receive = (eventType: string, raw: Event) => {
      const message = raw as MessageEvent<string>;
      if (message.lastEventId) {
        this.lastEventId = message.lastEventId;
        if (this.seen.has(message.lastEventId)) return;
        this.seen.add(message.lastEventId);
        this.order.push(message.lastEventId);
        if (this.order.length > 500) this.seen.delete(this.order.shift() || "");
      }
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(message.data || "{}");
      } catch {
        data = { raw: message.data };
      }
      this.handler({ id: message.lastEventId || undefined, type: eventType, data });
    };
    for (const type of KNOWN_EVENTS) source.addEventListener(type, (event) => receive(type, event));
    source.onerror = () => {
      if (this.closed) return;
      source.close();
      this.source = null;
      if (this.retryCount >= this.maxRetries) {
        this.statusHandler("disconnected");
        return;
      }
      this.retryCount += 1;
      const delay = Math.min(1000 * 2 ** (this.retryCount - 1), 15000);
      this.statusHandler("reconnecting");
      this.retryTimer = window.setTimeout(() => this.open(), delay);
    };
  }
}
