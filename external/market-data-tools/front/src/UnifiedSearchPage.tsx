import { useMemo, useState } from "react";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  FileJson,
  Play,
  RotateCcw,
  Search,
  Server,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import { formatJson, streamUnifiedSearch } from "./api";
import type {
  ServerEvent,
  StageEvent,
  PublicSearchResponse,
  UnifiedSearchOptions,
  UnifiedSearchResult,
} from "./types";

/**
 * 统一入口的阶段名称由后端事件决定；这里仅提供中文显示名称，不参与业务路由。
 * 没有登记的阶段会使用后端传来的原始名称，保证新增适配器时前端仍能观察链路。
 */
const STAGE_LABELS: Record<string, { label: string; category: string }> = {
  query_understanding: { label: "查询理解和主体提取", category: "对话大模型" },
  dataset_exact_match: { label: "数据集精确匹配", category: "检索" },
  dataset_keyword_search: { label: "数据集关键词检索", category: "检索" },
  dataset_pg_trgm_search: { label: "数据集字符模糊检索", category: "检索" },
  dataset_embedding_search: { label: "数据集 Embedding 语义检索", category: "模型" },
  dataset_rrf_merge: { label: "数据集 RRF 合并", category: "程序" },
  dataset_catalog: { label: "dataset_catalog 正式回查", category: "数据库" },
  dataset_candidate_selector: { label: "数据集候选大模型判断", category: "对话大模型" },
  dataset_consistency_check: { label: "数据集意图一致性校验", category: "程序" },
  market_bar_request: { label: "历史行情日期解析", category: "程序" },
  macro_observation_request: { label: "宏观查询条件解析", category: "程序" },
  news_date_request: { label: "新闻日期解析", category: "程序" },
  exact_match: { label: "金融工具精确匹配", category: "检索" },
  keyword_search: { label: "金融工具关键词检索", category: "检索" },
  pg_trgm_search: { label: "金融工具字符模糊检索", category: "检索" },
  embedding_search: { label: "金融工具 Embedding 语义检索", category: "模型" },
  rrf_merge: { label: "金融工具 RRF 合并", category: "程序" },
  instrument_master: { label: "instrument_master 校验", category: "数据库" },
  candidate_selector: { label: "金融工具候选大模型判断", category: "对话大模型" },
  instrument_identifier: { label: "instrument_identifier 有效期校验", category: "数据库" },
  dataset_field_catalog: { label: "dataset_field_catalog 字段读取", category: "数据库" },
  news_exact_match: { label: "新闻精确匹配", category: "检索" },
  news_keyword_search: { label: "新闻关键词检索", category: "检索" },
  news_pg_trgm_search: { label: "新闻字符模糊检索", category: "检索" },
  news_embedding_search: { label: "新闻 Embedding 语义检索", category: "模型" },
  news_rrf_merge: { label: "新闻 RRF 合并", category: "程序" },
  business_adapter_query: { label: "业务适配器查询", category: "业务表" },
};

const DEFAULT_OPTIONS: UnifiedSearchOptions = {
  query: "查询 EURUSD 的最新价格",
  limit: 3,
  provider: null,
  use_embedding: true,
  // 统一入口必须经过候选筛选模型；保留字段是为了兼容现有 SDK 请求结构。
  use_candidate_llm: true,
  row_limit: 100,
  start_date: null,
  end_date: null,
};

function statusLabel(status: StageEvent["status"] | undefined): string {
  if (status === "running") return "运行中";
  if (status === "completed") return "已完成";
  if (status === "error") return "失败";
  if (status === "skipped") return "已跳过";
  return "等待";
}

function stageIcon(status: StageEvent["status"] | undefined) {
  if (status === "completed") return <Check size={15} strokeWidth={2.5} />;
  if (status === "error") return <X size={15} strokeWidth={2.5} />;
  if (status === "running") return <CircleDot className="pulse" size={15} />;
  return <ChevronRight size={15} />;
}

function stageDefinition(stageId: string): { label: string; category: string } {
  return STAGE_LABELS[stageId] ?? { label: stageId, category: "阶段" };
}

function formatCell(value: unknown): string {
  if (value == null || value === "") return "-";
  if (Array.isArray(value)) return value.join(" · ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finalResult(result: UnifiedSearchResult | null): Record<string, unknown> | null {
  if (!result) return null;
  if (result.execution) return result.execution;
  if (result.price_result) return result.price_result as unknown as Record<string, unknown>;
  if (result.market_bars_result) return result.market_bars_result as unknown as Record<string, unknown>;
  if (result.macro_observations_result) return result.macro_observations_result as unknown as Record<string, unknown>;
  if (result.news_result) return result.news_result as unknown as Record<string, unknown>;
  return null;
}

function publicResponse(result: UnifiedSearchResult | null): PublicSearchResponse {
  // 调试 SSE 仍携带完整内部结果；这里把它转换成正式服务对外使用的精简协议。
  if (!result) return { status: "error", data: [] };

  const execution = finalResult(result);
  if (!execution) {
    return {
      status: "error",
      data: [],
      code: "INTERNAL_RESULT_INVALID",
      message: "查询服务未生成有效业务结果",
    };
  }

  const rawRows = execution.rows;
  const rows = Array.isArray(rawRows) ? rawRows : [];
  // 统一入口和兼容独立入口的完整调试响应可能把 adapter 放在不同层级；公开展示
  // 必须依据实际适配器去掉宏观、新闻记录中的 metadata。
  const adapter = String(execution.adapter ?? result.adapter ?? "");
  const data = rows.flatMap((row): Array<Record<string, unknown>> => {
    if (!isRecord(row)) return [];

    // 宏观和新闻内部需要保留 metadata 供调试阶段使用，正式响应只显示 data。
    if (adapter === "macro_observations" || adapter === "news_articles") {
      return isRecord(row.data) ? [row.data] : [];
    }
    return [row];
  });

  const executionStatus = String(execution.status ?? "");
  if (executionStatus === "resolved" || executionStatus === "not_found") {
    return { status: "success", data };
  }

  return {
    status: "rejected",
    data: [],
    code: typeof execution.code === "string" ? execution.code : "QUERY_REJECTED",
    message: typeof execution.reason === "string" ? execution.reason : "查询未完成",
  };
}

function finalStatus(result: UnifiedSearchResult | null): string {
  return publicResponse(result).status;
}

function resultSummary(result: UnifiedSearchResult | null): { value: string; meta: string } {
  if (!result?.routing) {
    return { value: "等待统一查询", meta: "输入自然语言后，系统会从数据集目录中选择业务数据集。" };
  }
  const resolution = result.dataset_resolution;
  const dataset = resolution?.dataset_id ?? "未确认数据集";
  const table = resolution?.storage_table_name ?? "未确认业务表";
  const publicPayload = publicResponse(result);
  const rows = publicPayload.data.length;
  return {
    value: `${dataset} · ${finalStatus(result)}`,
    meta: `${table} · ${result.routing.adapter ?? "未选择适配器"} · 返回 ${String(rows)} 行`,
  };
}

interface UnifiedSearchPageProps {
  onHome: () => void;
}

export function UnifiedSearchPage({ onHome }: UnifiedSearchPageProps) {
  const [query, setQuery] = useState(DEFAULT_OPTIONS.query);
  const [limit, setLimit] = useState(DEFAULT_OPTIONS.limit);
  const [provider, setProvider] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [useEmbedding, setUseEmbedding] = useState(true);
  const [stages, setStages] = useState<Record<string, StageEvent>>({});
  const [stageOrder, setStageOrder] = useState<string[]>([]);
  const [selectedStage, setSelectedStage] = useState("query_understanding");
  const [result, setResult] = useState<UnifiedSearchResult | null>(null);
  const [running, setRunning] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const activeStage = stages[selectedStage];
  const completedCount = Object.values(stages).filter((stage) => stage.status === "completed").length;
  const errorCount = Object.values(stages).filter((stage) => stage.status === "error").length;
  const summary = useMemo(() => resultSummary(result), [result]);
  const resultStatus = finalStatus(result);
  const candidates = result?.dataset_search?.candidates ?? [];
  const consistency = result?.dataset_search?.consistency_check ?? result?.dataset_consistency_check;

  const handleEvent = (event: ServerEvent) => {
    if (event.type === "stage") {
      setStages((current) => ({ ...current, [event.payload.stage]: event.payload }));
      setStageOrder((current) => (
        current.includes(event.payload.stage) ? current : [...current, event.payload.stage]
      ));
    } else if (event.type === "result") {
      setResult(event.payload as UnifiedSearchResult);
    } else if (event.type === "error") {
      setRequestError(event.payload.error_type + ": " + event.payload.message);
    }
  };

  const runQuery = async () => {
    if (!query.trim() || running) return;
    setRunning(true);
    setRequestError(null);
    setResult(null);
    setStages({});
    setStageOrder([]);
    setSelectedStage("query_understanding");
    try {
      for await (const event of streamUnifiedSearch({
        query: query.trim(),
        limit,
        provider: provider.trim() || null,
        use_embedding: useEmbedding,
        // 候选模型是统一接口的一致性闸门，不允许在前端关闭。
        use_candidate_llm: true,
        row_limit: 100,
        start_date: startDate || null,
        end_date: endDate || null,
      })) {
        handleEvent(event);
      }
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : String(error));
    } finally {
      setRunning(false);
    }
  };

  const resetQuery = () => {
    setQuery(DEFAULT_OPTIONS.query);
    setLimit(DEFAULT_OPTIONS.limit);
    setProvider("");
    setStartDate("");
    setEndDate("");
    setUseEmbedding(true);
    setStages({});
    setStageOrder([]);
    setSelectedStage("query_understanding");
    setResult(null);
    setRequestError(null);
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand-lockup brand-button" onClick={onHome} title="返回首页">
          <span className="brand-mark"><Search size={19} /></span>
          <span>
            <span className="eyebrow">ICBC TRADING</span>
            <span className="brand-title">AI Search Workbench</span>
          </span>
        </button>
        <div className="topbar-status">
          <span className="status-dot" />
          <span>{running ? "统一查询进行中" : "目录驱动统一入口"}</span>
          <span className="status-divider" />
          <span className="mono">{completedCount}/{stageOrder.length || 0} stages</span>
        </div>
      </header>

      <main className="workspace route-workspace">
        <div className="route-breadcrumb">
          <button className="back-home-button" onClick={onHome}>
            <ArrowLeft size={16} />
            返回首页
          </button>
          <span className="breadcrumb-divider">/</span>
          <span>统一查询 / 数据集目录</span>
        </div>

        <section className="query-band">
          <div className="section-heading">
            <div>
              <p className="eyebrow">UNIFIED DATASET ENTRY</p>
              <h2>统一查询</h2>
              <p className="section-note">输入自然语言问题，由数据集目录候选决定业务表和查询适配器。</p>
            </div>
            <div className="query-stats">
              <span><Server size={14} /> 统一入口</span>
              <span><Sparkles size={14} /> 目录驱动</span>
            </div>
          </div>

          <div className="query-form unified-query-form">
            <label className="query-input-wrap">
              <span>自然语言输入</span>
              <div className="query-input-line">
                <Search size={17} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => { if (event.key === "Enter") void runQuery(); }}
                  placeholder="例如：查询 EURUSD 最近一个月的相关新闻"
                  spellCheck={false}
                />
              </div>
            </label>
            <label className="compact-field">
              <span>候选上限</span>
              <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
                <option value={1}>1</option>
                <option value={3}>3</option>
                <option value={5}>5</option>
                <option value={10}>10</option>
              </select>
            </label>
            <label className="compact-field provider-field">
              <span>供应商筛选</span>
              <input
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
                placeholder="可选，例如 LSEG"
              />
            </label>
            <label className="compact-field">
              <span>开始日期</span>
              <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </label>
            <label className="compact-field">
              <span>结束日期</span>
              <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </label>
            <button
              className="primary-button"
              onClick={() => void runQuery()}
              disabled={running || !query.trim()}
              title="运行统一查询"
            >
              <Play size={16} fill="currentColor" />
              {running ? "运行中" : "运行查询"}
            </button>
            <button className="icon-button" onClick={resetQuery} disabled={running} title="重置查询条件">
              <RotateCcw size={17} />
            </button>
          </div>

          <div className="toggle-row">
            <label className="toggle-control">
              <input type="checkbox" checked={useEmbedding} onChange={(event) => setUseEmbedding(event.target.checked)} />
              <span className="toggle-visual" />
              <span>Embedding 语义检索</span>
            </label>
            <span className="status-pill resolved">数据集候选大模型必需</span>
            {requestError && <span className="inline-error"><TriangleAlert size={15} /> {requestError}</span>}
          </div>
        </section>

        <section className="result-strip unified-result-strip">
          <div className="result-main">
            <div className="result-icon"><Sparkles size={18} /></div>
            <div>
              <p className="eyebrow">DATASET RESULT</p>
              <h2>{summary.value}</h2>
              <p>{summary.meta}</p>
            </div>
          </div>
          <div className="result-metrics">
            <div className="route-metric">
              <span>数据集</span>
              <strong>{formatCell(result?.dataset_resolution?.dataset_id)}</strong>
            </div>
            <div>
              <span>业务表</span>
              <strong>{formatCell(result?.dataset_resolution?.storage_table_name)}</strong>
            </div>
            <div>
              <span>统一结果</span>
              <strong>{resultStatus}</strong>
            </div>
            <div>
              <span>完成阶段</span>
              <strong>{completedCount}</strong>
            </div>
            <div>
              <span>阶段错误</span>
              <strong className={errorCount ? "danger-text" : ""}>{errorCount}</strong>
            </div>
          </div>
        </section>

        <section className="candidate-panel unified-route-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">DATASET CANDIDATES</p>
              <h2>数据集目录候选</h2>
            </div>
            <span className={`status-pill ${String(consistency?.status ?? "pending").toLowerCase()}`}>
              {formatCell(consistency?.status ?? "等待校验")}
            </span>
          </div>
          <div className="candidate-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>dataset_id</th>
                  <th>dataset_name</th>
                  <th>data_category</th>
                  <th>provider</th>
                  <th>frequency</th>
                  <th>检索证据</th>
                  <th>RRF</th>
                  <th>目录状态</th>
                </tr>
              </thead>
              <tbody>
                {candidates.length ? candidates.map((candidate, index) => (
                  <tr key={`${String(candidate.dataset_id)}-${index}`}>
                    <td className="mono strong-cell">{formatCell(candidate.dataset_id)}</td>
                    <td>{formatCell(candidate.dataset_name)}</td>
                    <td>{formatCell(candidate.data_category)}</td>
                    <td className="mono">{formatCell(candidate.provider)}</td>
                    <td className="mono">{formatCell(candidate.frequency)}</td>
                    <td className="method-list">{formatCell(candidate.matched_by)}</td>
                    <td className="mono">{formatCell(candidate.rrf_score)}</td>
                    <td>{formatCell(candidate.resolution_status)}</td>
                  </tr>
                )) : <tr><td colSpan={8} className="empty-state">运行查询后显示数据库返回的数据集候选</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="unified-response-meta">
            <span><strong>模型选择：</strong>{formatCell(result?.dataset_search?.model_selection)}</span>
            <span><strong>一致性：</strong>{formatCell(consistency?.reason ?? "候选返回后显示校验结果")}</span>
          </div>
        </section>

        <div className="trace-grid">
          <section className="trace-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">UNIFIED TRACE</p>
                <h2>统一入口完整链路</h2>
              </div>
              <span className="panel-count">{stageOrder.length} events</span>
            </div>
            <div className="stage-list">
              {stageOrder.length ? stageOrder.map((stageId, index) => {
                const stage = stages[stageId];
                const definition = stageDefinition(stageId);
                const isSelected = selectedStage === stageId;
                return (
                  <button
                    className={"stage-row " + (isSelected ? "is-selected " : "") + (stage?.status ?? "is-pending")}
                    key={stageId}
                    onClick={() => setSelectedStage(stageId)}
                  >
                    <span className="stage-index">{String(index + 1).padStart(2, "0")}</span>
                    <span className="stage-status-icon">{stageIcon(stage?.status)}</span>
                    <span className="stage-copy">
                      <strong>{definition.label}</strong>
                      <small>{definition.category}{stage?.duration_ms != null ? " · " + String(stage.duration_ms) + " ms" : ""}</small>
                    </span>
                    <span className={"stage-status " + (stage?.status ?? "is-pending")}>{statusLabel(stage?.status)}</span>
                    <ChevronRight className="stage-arrow" size={16} />
                  </button>
                );
              }) : <div className="empty-state">运行统一查询后显示实际执行阶段</div>}
            </div>
          </section>

          <section className="detail-panel">
            <div className="panel-heading detail-heading">
              <div>
                <p className="eyebrow">MODULE INSPECTOR</p>
                <h2>{stageDefinition(selectedStage).label}</h2>
              </div>
              <div className="detail-status">{stageIcon(activeStage?.status)} {statusLabel(activeStage?.status)}</div>
            </div>
            <div className="detail-meta">
              <span><Clock3 size={14} /> {activeStage?.duration_ms != null ? String(activeStage.duration_ms) + " ms" : "未运行"}</span>
              <span><FileJson size={14} /> input / output</span>
            </div>
            <div className="json-columns">
              <div className="json-block"><div className="json-label">INPUT</div><pre>{formatJson(activeStage?.input)}</pre></div>
              <div className="json-block output-block"><div className="json-label">OUTPUT</div><pre>{formatJson(activeStage?.output)}</pre></div>
            </div>
            {activeStage?.error && <div className="error-box"><TriangleAlert size={16} /><span>{activeStage.error}</span></div>}
          </section>
        </div>

        <section className="candidate-panel unified-response-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">UNIFIED RESPONSE</p><h2>统一接口返回</h2></div>
            <span className="status-pill">{result ? resultStatus : "等待查询"}</span>
          </div>
          <div className="unified-response-meta">
            <span><strong>协议：</strong>status + data</span>
            <span><strong>记录数：</strong>{publicResponse(result).data.length}</span>
            {publicResponse(result).code && <span><strong>错误码：</strong>{publicResponse(result).code}</span>}
          </div>
          <pre className="unified-response-pre">{formatJson(publicResponse(result))}</pre>
        </section>
      </main>
    </div>
  );
}
