import type { SearchOptions, ServerEvent, UnifiedSearchOptions } from "./types";

/**
 * 读取 POST SSE 接口返回的事件流。
 *
 * 浏览器原生 EventSource 只支持 GET，查询参数又需要通过 POST 传递，因此这里使用
 * fetch + ReadableStream 手动解析 SSE。解析层只负责协议，不修改后端业务事件内容。
 */
async function* streamEndpoint(
  endpoint: string,
  options: SearchOptions | UnifiedSearchOptions,
): AsyncGenerator<ServerEvent> {
  // 前端页面内部仍保留 route 以便选择测试页面，但 tools 后端不接收 route、
  // Embedding 开关或候选模型开关。这里把页面状态转换成五个工具的公共参数。
  const { route, use_embedding, use_candidate_llm, row_limit, limit, ...rest } = options as SearchOptions;
  const payload = {
    ...rest,
    max_rows: row_limit ?? 100,
  };
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`查询接口返回 HTTP ${response.status}`);
  }
  if (!response.body) {
    throw new Error("查询接口没有返回 SSE 数据流");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const parseBlock = (block: string): ServerEvent | null => {
    let eventType = "message";
    const dataLines: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event: ")) eventType = line.slice(7).trim();
      if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
    if (dataLines.length === 0) return null;
    return { type: eventType, payload: JSON.parse(dataLines.join("\n")) } as ServerEvent;
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = parseBlock(block.trim());
      if (event) yield event;
    }
    if (done) break;
  }

  const finalEvent = parseBlock(buffer.trim());
  if (finalEvent) yield finalEvent;
}

/** 调用指定业务工具的 SSE 接口；route 只用于前端选择对应工具路径。 */
export async function* streamSearch(options: SearchOptions): AsyncGenerator<ServerEvent> {
  const endpoints: Record<SearchOptions["route"], string> = {
    latest_prices: "/tools/latest_prices_search/stream",
    market_bars: "/tools/market_bars_search/stream",
    macro_observations: "/tools/macro_observations_search/stream",
    news_articles: "/tools/news_articles_search/stream",
  };
  yield* streamEndpoint(endpoints[options.route], options);
}

/** 调用统一自然语言 SSE 接口；路线由后端模型识别后自动分发。 */
export async function* streamUnifiedSearch(
  options: UnifiedSearchOptions,
): AsyncGenerator<ServerEvent> {
  yield* streamEndpoint("/tools/unified_search/stream", options);
}

/** 将任意后端输出稳定格式化，保证 JSON 查看器不会因 null 或字符串报错。 */
export function formatJson(value: unknown): string {
  if (value === undefined) return "(无输出)";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2) ?? "null";
}
