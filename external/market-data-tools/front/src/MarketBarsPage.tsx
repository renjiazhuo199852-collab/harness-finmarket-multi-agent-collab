import { useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Database,
  FileJson,
  Play,
  RotateCcw,
  Search,
  Server,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import { formatJson, streamSearch } from "./api";
import type {
  SearchOptions,
  SearchResult,
  ServerEvent,
  StageEvent,
} from "./types";

interface StageDefinition {
  id: string;
  label: string;
  category: string;
}

const MARKET_BAR_STAGES: StageDefinition[] = [
  { id: "query_understanding", label: "查询理解和主体提取", category: "对话大模型" },
  { id: "dataset_exact_match", label: "数据集精确匹配", category: "检索" },
  { id: "dataset_keyword_search", label: "数据集关键词检索", category: "检索" },
  { id: "dataset_pg_trgm_search", label: "数据集 pg_trgm 模糊检索", category: "检索" },
  { id: "dataset_embedding_search", label: "数据集 Embedding 语义检索", category: "模型" },
  { id: "dataset_rrf_merge", label: "数据集 RRF 合并", category: "程序" },
  { id: "dataset_catalog", label: "dataset_catalog 正式回查", category: "数据库" },
  { id: "dataset_candidate_selector", label: "数据集候选大模型筛选", category: "模型" },
  { id: "dataset_consistency_check", label: "数据集一致性校验", category: "程序" },
  { id: "compatibility_route_check", label: "独立页面范围校验", category: "程序" },
  { id: "dataset_field_catalog", label: "dataset_field_catalog 字段解析", category: "数据库" },
  { id: "exact_match", label: "金融工具精确匹配", category: "检索" },
  { id: "keyword_search", label: "金融工具关键词检索", category: "检索" },
  { id: "pg_trgm_search", label: "金融工具 pg_trgm 模糊检索", category: "检索" },
  { id: "embedding_search", label: "金融工具 Embedding 语义检索", category: "模型" },
  { id: "rrf_merge", label: "金融工具 RRF 合并", category: "程序" },
  { id: "instrument_master", label: "instrument_master 校验", category: "数据库" },
  { id: "candidate_selector", label: "金融工具候选大模型筛选", category: "模型" },
  { id: "instrument_identifier", label: "instrument_identifier 有效期校验", category: "数据库" },
  { id: "market_bars_query", label: "market_bars 日线查询", category: "业务表" },
];

const DEFAULT_OPTIONS: SearchOptions = {
  query: "查询 EURUSD 最近一个月的日K线",
  route: "market_bars",
  limit: 3,
  provider: null,
  use_embedding: true,
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

function marketSummary(result: SearchResult | null): { value: string; meta: string } {
  const routeGuard = result?.route_guard;
  if (routeGuard && !routeGuard.accepted) {
    return {
      value: "路线已停止",
      meta: `${routeGuard.reason} · 识别为 ${routeGuard.recognized_route}`,
    };
  }
  const request = result?.market_bar_request;
  if (request?.status !== "resolved") {
    return {
      value: "查询未执行",
      meta: request?.reason ?? "等待日期和日线参数解析",
    };
  }
  const bars = result?.market_bars_result;
  const selected = result?.model_selection?.candidate as Record<string, unknown> | undefined;
  const identifier = result?.identifier_resolution?.selected as Record<string, unknown> | undefined;
  if (bars?.status === "resolved") {
    return {
      value: `${String(selected?.canonical_symbol ?? "工具")} 日线`,
      meta: `${String(bars.row_count ?? 0)} 行 · ${String(bars.start_date ?? request.start_date)} 至 ${String(bars.end_date ?? request.end_date)}`,
    };
  }
  if (result?.dataset_resolution?.status === "resolved") {
    return {
      value: String(result.dataset_resolution.storage_table_name ?? "market_bars"),
      meta: `${String(result.dataset_resolution.dataset_id ?? "")} · ${String(identifier?.provider ?? "供应商待定")}`,
    };
  }
  if (result?.model_selection?.decision === "select") {
    return {
      value: String(result.model_selection.instrument_id ?? "工具已确认"),
      meta: `${String(selected?.canonical_symbol ?? "")} · ${String(identifier?.provider ?? "供应商待定")} / ${String(identifier?.identifier ?? "标识待定")}`,
    };
  }
  return {
    value: "未确定",
    meta: String(result?.model_selection?.reason ?? "尚未形成最终工具选择"),
  };
}

function formatCell(value: unknown): string {
  return value == null ? "-" : String(value);
}

interface MarketBarsPageProps {
  onHome: () => void;
}

export function MarketBarsPage({ onHome }: MarketBarsPageProps) {
  const [query, setQuery] = useState(DEFAULT_OPTIONS.query);
  const [limit, setLimit] = useState(DEFAULT_OPTIONS.limit);
  const [rowLimit, setRowLimit] = useState(DEFAULT_OPTIONS.row_limit ?? 100);
  const [provider, setProvider] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [useEmbedding, setUseEmbedding] = useState(true);
  const [useCandidateLlm, setUseCandidateLlm] = useState(true);
  const [stages, setStages] = useState<Record<string, StageEvent>>({});
  const [result, setResult] = useState<SearchResult | null>(null);
  const [selectedStage, setSelectedStage] = useState("query_understanding");
  const [running, setRunning] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const activeStage = stages[selectedStage];
  const summary = useMemo(() => marketSummary(result), [result]);
  const completedCount = Object.values(stages).filter((stage) => stage.status === "completed").length;
  const errorCount = Object.values(stages).filter((stage) => stage.status === "error").length;
  const barResult = result?.market_bars_result;
  const barRows = barResult?.rows ?? [];

  /** SSE 阶段事件直接按 stage 名保存，保证每个模块的 input/output 可独立查看。 */
  const handleEvent = (event: ServerEvent) => {
    if (event.type === "stage") {
      setStages((current) => ({ ...current, [event.payload.stage]: event.payload }));
    } else if (event.type === "result") {
      setResult(event.payload);
    } else if (event.type === "error") {
      setRequestError(`${event.payload.error_type}: ${event.payload.message}`);
    }
  };

  const runQuery = async () => {
    if (!query.trim() || running) return;
    setRunning(true);
    setRequestError(null);
    setResult(null);
    setStages({});
    setSelectedStage("query_understanding");
    try {
      for await (const event of streamSearch({
        query: query.trim(),
        route: DEFAULT_OPTIONS.route,
        limit,
        provider: provider.trim() || null,
        use_embedding: useEmbedding,
        use_candidate_llm: useCandidateLlm,
        row_limit: rowLimit,
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
    setRowLimit(DEFAULT_OPTIONS.row_limit ?? 100);
    setProvider("");
    setStartDate("");
    setEndDate("");
    setUseEmbedding(true);
    setUseCandidateLlm(true);
    setStages({});
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
          <span>{running ? "查询进行中" : "历史行情查询"}</span>
          <span className="status-divider" />
          <span className="mono">{completedCount}/{MARKET_BAR_STAGES.length} stages</span>
        </div>
      </header>

      <main className="workspace route-workspace">
        <div className="route-breadcrumb">
          <button className="back-home-button" onClick={onHome}>
            <ArrowLeft size={16} />
            返回首页
          </button>
          <span className="breadcrumb-divider">/</span>
          <span>market_bars</span>
        </div>

        <section className="query-band">
          <div className="section-heading">
            <div>
              <p className="eyebrow">MARKET BARS ROUTE</p>
              <h2>历史行情查询</h2>
            </div>
            <div className="query-stats">
              <span><Server size={14} /> SSE trace</span>
              <span><Database size={14} /> market_bars</span>
            </div>
          </div>
          <div className="query-form market-query-form">
            <label className="query-input-wrap">
              <span>自然语言输入</span>
              <div className="query-input-line">
                <Search size={17} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => { if (event.key === "Enter") void runQuery(); }}
                  placeholder="例如：查询 EURUSD 最近一个月的日K线"
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
            <label className="compact-field">
              <span>开始日期</span>
              <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </label>
            <label className="compact-field">
              <span>结束日期</span>
              <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </label>
            <label className="compact-field">
              <span>返回行数</span>
              <select value={rowLimit} onChange={(event) => setRowLimit(Number(event.target.value))}>
                <option value={30}>30</option>
                <option value={100}>100</option>
                <option value={250}>250</option>
                <option value={1000}>1000</option>
              </select>
            </label>
            <label className="compact-field provider-field">
              <span>供应商筛选</span>
              <input value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="可选，例如 LSEG" />
            </label>
            <button className="primary-button" onClick={() => void runQuery()} disabled={running || !query.trim()} title="运行历史行情查询">
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
            <label className="toggle-control">
              <input type="checkbox" checked={useCandidateLlm} onChange={(event) => setUseCandidateLlm(event.target.checked)} />
              <span className="toggle-visual" />
              <span>候选筛选大模型</span>
            </label>
            {requestError && <span className="inline-error"><AlertCircle size={15} /> {requestError}</span>}
          </div>
        </section>

        <section className="result-strip">
          <div className="result-main">
            <div className="result-icon"><Sparkles size={18} /></div>
            <div>
              <p className="eyebrow">RESOLUTION</p>
              <h2>{summary.value}</h2>
              <p>{summary.meta}</p>
            </div>
          </div>
          <div className="result-metrics">
            <div className="route-metric"><span>测试范围</span><strong>{result?.query_intent?.route ?? "-"}</strong></div>
            <div><span>完成阶段</span><strong>{completedCount}</strong></div>
            <div><span>阶段错误</span><strong className={errorCount ? "danger-text" : ""}>{errorCount}</strong></div>
            <div><span>工具候选</span><strong>{result?.candidates?.length ?? "-"}</strong></div>
            <div><span>频率</span><strong>{result?.market_bar_request?.frequency ?? "-"}</strong></div>
            <div><span>行情行</span><strong>{barResult?.row_count ?? "-"}</strong></div>
          </div>
        </section>

        <div className="trace-grid">
          <section className="trace-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">PIPELINE TRACE</p>
                <h2>历史行情链路</h2>
              </div>
              <span className="panel-count">{Object.keys(stages).length} events</span>
            </div>
            <div className="stage-list">
              {MARKET_BAR_STAGES.map((definition, index) => {
                const stage = stages[definition.id];
                const isSelected = selectedStage === definition.id;
                return (
                  <button
                    className={`stage-row ${isSelected ? "is-selected" : ""} ${stage?.status ?? "is-pending"}`}
                    key={definition.id}
                    onClick={() => setSelectedStage(definition.id)}
                  >
                    <span className="stage-index">{String(index + 1).padStart(2, "0")}</span>
                    <span className="stage-status-icon">{stageIcon(stage?.status)}</span>
                    <span className="stage-copy">
                      <strong>{definition.label}</strong>
                      <small>{definition.category}{stage?.duration_ms != null ? ` · ${stage.duration_ms} ms` : ""}</small>
                    </span>
                    <span className={`stage-status ${stage?.status ?? "is-pending"}`}>{statusLabel(stage?.status)}</span>
                    <ChevronRight className="stage-arrow" size={16} />
                  </button>
                );
              })}
            </div>
          </section>

          <section className="detail-panel">
            <div className="panel-heading detail-heading">
              <div>
                <p className="eyebrow">MODULE INSPECTOR</p>
                <h2>{MARKET_BAR_STAGES.find((stage) => stage.id === selectedStage)?.label ?? selectedStage}</h2>
              </div>
              <div className="detail-status">{stageIcon(activeStage?.status)} {statusLabel(activeStage?.status)}</div>
            </div>
            <div className="detail-meta">
              <span><Clock3 size={14} /> {activeStage?.duration_ms != null ? `${activeStage.duration_ms} ms` : "未运行"}</span>
              <span><FileJson size={14} /> input / output</span>
            </div>
            <div className="json-columns">
              <div className="json-block">
                <div className="json-label">INPUT</div>
                <pre>{formatJson(activeStage?.input)}</pre>
              </div>
              <div className="json-block output-block">
                <div className="json-label">OUTPUT</div>
                <pre>{formatJson(activeStage?.output)}</pre>
              </div>
            </div>
            {activeStage?.error && <div className="error-box"><TriangleAlert size={16} /><span>{activeStage.error}</span></div>}
          </section>
        </div>

        <section className="candidate-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">REQUEST PLAN</p><h2>日线查询参数</h2></div>
            <span className={`status-pill ${String(result?.market_bar_request?.status ?? "pending").toLowerCase()}`}>
              {result?.market_bar_request?.status ?? "等待解析"}
            </span>
          </div>
          <div className="json-columns market-request-columns">
            <div className="json-block"><div className="json-label">PARSED REQUEST</div><pre>{formatJson(result?.market_bar_request)}</pre></div>
            <div className="json-block output-block"><div className="json-label">FIELD PLAN</div><pre>{formatJson(result?.field_resolution)}</pre></div>
          </div>
        </section>

        <section className="candidate-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">CANDIDATES</p><h2>候选工具</h2></div>
            <span className="panel-count">RRF → master → LLM</span>
          </div>
          <div className="candidate-table-wrap">
            <table>
              <thead><tr><th>canonical_symbol</th><th>instrument_id</th><th>name</th><th>status</th><th>matched_by</th><th>rrf_score</th></tr></thead>
              <tbody>
                {result?.candidates?.length ? result.candidates.map((candidate, index) => (
                  <tr key={`${String(candidate.canonical_symbol)}-${index}`}>
                    <td className="mono strong-cell">{formatCell(candidate.canonical_symbol)}</td>
                    <td className="mono">{formatCell(candidate.instrument_id)}</td>
                    <td>{formatCell(candidate.master_name ?? candidate.name)}</td>
                    <td><span className={`status-pill ${String(candidate.status ?? "unknown").toLowerCase()}`}>{formatCell(candidate.status)}</span></td>
                    <td className="method-list">{Array.isArray(candidate.matched_by) ? candidate.matched_by.join(" · ") : "-"}</td>
                    <td className="mono">{formatCell(candidate.rrf_score)}</td>
                  </tr>
                )) : <tr><td colSpan={6} className="empty-state">运行查询后显示候选</td></tr>}
              </tbody>
            </table>
          </div>
          {result?.warnings?.map((warning) => <div className="warning-line" key={warning}><TriangleAlert size={15} /> {warning}</div>)}
        </section>

        <section className="candidate-panel dataset-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">DATASET CANDIDATES</p><h2>候选数据集</h2></div>
            <span className="panel-count">RRF → source.dataset_catalog → LLM</span>
          </div>
          <div className="candidate-table-wrap">
            <table>
              <thead><tr><th>dataset_id</th><th>provider</th><th>frequency</th><th>data_category</th><th>storage_table_name</th><th>status</th></tr></thead>
              <tbody>
                {result?.dataset_search?.candidates?.length ? result.dataset_search.candidates.map((candidate, index) => (
                  <tr key={`${String(candidate.dataset_id)}-${index}`}>
                    <td className="mono strong-cell">{formatCell(candidate.dataset_id)}</td>
                    <td className="mono">{formatCell(candidate.provider)}</td>
                    <td className="mono">{formatCell(candidate.frequency)}</td>
                    <td>{formatCell(candidate.data_category)}</td>
                    <td className="mono">{formatCell(candidate.storage_table_name)}</td>
                    <td><span className={`status-pill ${String(candidate.resolution_status ?? "unknown").toLowerCase()}`}>{formatCell(candidate.resolution_status)}</span></td>
                  </tr>
                )) : <tr><td colSpan={6} className="empty-state">工具和供应商标识确认后显示数据集候选</td></tr>}
              </tbody>
            </table>
          </div>
          {result?.dataset_search?.warnings?.map((warning) => <div className="warning-line" key={warning}><TriangleAlert size={15} /> {warning}</div>)}
          {result?.dataset_resolution?.status === "resolved" && (
            <div className="dataset-resolution-line"><Database size={15} /><span>已确认：<strong className="mono">{result.dataset_resolution.dataset_id}</strong> → <strong className="mono">{result.dataset_resolution.storage_table_name}</strong></span></div>
          )}
        </section>

        <section className="candidate-panel market-bars-result-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">MARKET BARS RESULT</p><h2>日线 OHLCV 结果</h2></div>
            <span className={`status-pill ${String(barResult?.status ?? "pending").toLowerCase()}`}>{barResult?.status ?? "等待查询"}</span>
          </div>
          {barResult?.status === "resolved" && barRows.length ? (
            <>
              <div className="price-context">
                <span className="mono">{barResult.instrument_id ?? "-"}</span>
                <span>{barResult.provider ?? "-"} / {barResult.identifier ?? "-"}</span>
                <span className="mono">{barResult.storage_table_name ?? "-"}</span>
                <span>{barResult.frequency ?? "daily"}</span>
                <span>{barResult.start_date ?? "-"} 至 {barResult.end_date ?? "-"}</span>
              </div>
              <div className="candidate-table-wrap market-bars-table-wrap">
                <table>
                  <thead><tr><th>date</th><th>open</th><th>high</th><th>low</th><th>close</th><th>volume</th></tr></thead>
                  <tbody>
                    {barRows.map((row, index) => (
                      <tr key={`${String(row.date)}-${index}`}>
                        <td className="mono strong-cell">{formatCell(row.date)}</td>
                        <td className="mono">{formatCell(row.open)}</td>
                        <td className="mono">{formatCell(row.high)}</td>
                        <td className="mono">{formatCell(row.low)}</td>
                        <td className="mono strong-cell">{formatCell(row.close)}</td>
                        <td className="mono">{formatCell(row.volume)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="price-empty-state">{barResult?.reason ?? "完成前置目录和字段确认后显示日线结果"}</div>
          )}
        </section>
      </main>
    </div>
  );
}
