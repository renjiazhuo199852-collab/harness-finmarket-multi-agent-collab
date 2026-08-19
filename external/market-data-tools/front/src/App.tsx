import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Database,
  FileJson,
  GitBranch,
  House,
  Landmark,
  LineChart,
  Newspaper,
  Play,
  RotateCcw,
  Search,
  Server,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { formatJson, streamSearch } from "./api";
import { MacroObservationsPage } from "./MacroObservationsPage";
import { MarketBarsPage } from "./MarketBarsPage";
import { NewsArticlesPage } from "./NewsArticlesPage";
import { UnifiedSearchPage } from "./UnifiedSearchPage";
import type { SearchOptions, SearchResult, ServerEvent, StageEvent } from "./types";

type RouteId = "home" | "unified-search" | "latest-prices" | "macro-observations" | "market-bars" | "news-articles";

interface RouteDefinition {
  id: Exclude<RouteId, "home">;
  label: string;
  table: string;
  description: string;
  icon: LucideIcon;
  available: boolean;
}

const ROUTE_DEFINITIONS: RouteDefinition[] = [
  {
    id: "latest-prices",
    label: "最新价格",
    table: "latest_prices",
    description: "查询供应商最新现货报价和买卖价。",
    icon: Activity,
    available: true,
  },
  {
    id: "macro-observations",
    label: "宏观数据",
    table: "macro_observations",
    description: "宏观指标、历史值、预测值和修订值。",
    icon: Landmark,
    available: true,
  },
  {
    id: "market-bars",
    label: "历史行情",
    table: "market_bars",
    description: "按日期范围查询日线 OHLCV 行情数据。",
    icon: LineChart,
    available: true,
  },
  {
    id: "news-articles",
    label: "新闻资讯",
    table: "news_articles",
    description: "返回与查询主体文本或语义相关的新闻候选。",
    icon: Newspaper,
    available: true,
  },
];

const STAGE_DEFINITIONS = [
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
  { id: "business_adapter_query", label: "latest_prices 业务查询", category: "业务表" },
] as const;

const DEFAULT_OPTIONS: SearchOptions = {
  query: "查询 EURUSD 的最新价格",
  route: "latest_prices",
  limit: 3,
  provider: null,
  use_embedding: true,
  use_candidate_llm: true,
};

/** 根据地址栏 hash 读取页面，未知地址统一回到首页。 */
function routeFromHash(): RouteId {
  const route = window.location.hash.replace(/^#\/?/, "");
  if (
    route === "unified-search" ||
    route === "latest-prices" ||
    route === "macro-observations" ||
    route === "market-bars" ||
    route === "news-articles"
  ) {
    return route;
  }
  return "home";
}

/** 页面导航只修改 hash，不引入路由库，保证当前前端依赖保持轻量。 */
function navigateTo(route: RouteId): void {
  window.location.hash = route === "home" ? "#/" : `#/${route}`;
}

function statusLabel(status: StageEvent["status"] | undefined): string {
  if (status === "running") return "运行中";
  if (status === "completed") return "已完成";
  if (status === "error") return "失败";
  if (status === "skipped") return "已跳过";
  return "等待";
}

function selectedSummary(result: SearchResult | null): { value: string; meta: string } {
  const routeGuard = result?.route_guard;
  if (routeGuard && !routeGuard.accepted) {
    return {
      value: "路线已停止",
      meta: `${routeGuard.reason} · 识别为 ${routeGuard.recognized_route}`,
    };
  }
  const selection = result?.model_selection;
  const identifier = result?.identifier_resolution;
  const datasetResolution = result?.dataset_resolution;
  const selected = selection?.candidate as Record<string, unknown> | undefined;
  const identifierRow = identifier?.selected as Record<string, unknown> | undefined;
  const priceResult = result?.price_result;
  const priceRow = priceResult?.rows?.[0];
  if (priceResult?.status === "resolved" && priceRow) {
    const priceValue = priceRow.last ?? priceRow.mid ?? "价格已返回";
    return {
      value: String(priceValue),
      meta: `${String(selected?.canonical_symbol ?? "")} · ${String(priceResult.provider ?? "")} / ${String(priceResult.identifier ?? "")} · ${String(priceRow.price_time ?? "最新记录")}`,
    };
  }
  if (datasetResolution?.status === "resolved") {
    return {
      value: String(datasetResolution.storage_table_name ?? datasetResolution.dataset_id ?? "数据集已确认"),
      meta: `${String(datasetResolution.dataset_id ?? "")} · ${String(datasetResolution.provider ?? "供应商待定")} · ${String(datasetResolution.data_category ?? "")}`,
    };
  }
  if (selection?.decision === "select") {
    return {
      value: String(selection.instrument_id ?? "未返回"),
      meta: `${String(selected?.canonical_symbol ?? "")} · ${String(identifierRow?.provider ?? "供应商待定")} · ${String(identifierRow?.identifier ?? "标识待定")}`,
    };
  }
  if (result?.dataset_search?.model_selection?.decision === "needs_confirmation") {
    return {
      value: "数据集需要确认",
      meta: String(result.dataset_search.model_selection.reason ?? "多个数据集候选无法唯一确认"),
    };
  }
  return {
    value: selection?.decision === "needs_confirmation" ? "需要确认" : "未确定",
    meta: String(selection?.reason ?? "尚未形成最终工具选择"),
  };
}

function stageIcon(status: StageEvent["status"] | undefined) {
  if (status === "completed") return <Check size={15} strokeWidth={2.5} />;
  if (status === "error") return <X size={15} strokeWidth={2.5} />;
  if (status === "running") return <CircleDot className="pulse" size={15} />;
  return <ChevronRight size={15} />;
}

interface TopbarProps {
  statusText: string;
  stageCount?: string;
  onHome: () => void;
}

function Topbar({ statusText, stageCount, onHome }: TopbarProps) {
  return (
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
        <span>{statusText}</span>
        {stageCount && <><span className="status-divider" /><span className="mono">{stageCount}</span></>}
      </div>
    </header>
  );
}

function HomePage({ onNavigate }: { onNavigate: (route: RouteId) => void }) {
  return (
    <div className="app-shell">
      <Topbar statusText="本地测试环境" onHome={() => onNavigate("home")} />
      <main className="workspace home-workspace">
        <section className="home-heading">
          <p className="eyebrow">QUERY ROUTES</p>
          <h2>选择业务查询</h2>
          <p>四条业务路线独立运行，最新价格和历史行情已开放测试。</p>
        </section>
        <button className="unified-entry" onClick={() => onNavigate("unified-search")}>
          <span className="unified-entry-icon"><GitBranch size={23} /></span>
          <span className="unified-entry-copy">
            <span className="eyebrow">UNIFIED SDK ENTRY</span>
            <strong>统一查询</strong>
            <span>输入自然语言问题，自动识别路线并调用对应处理器。</span>
          </span>
          <span className="unified-entry-action">
            测试统一入口
            <ArrowRight size={17} />
          </span>
        </button>
        <section className="route-grid" aria-label="业务查询路线">
          {ROUTE_DEFINITIONS.map((route, index) => {
            const Icon = route.icon;
            return (
              <button
                className={`route-card ${route.available ? "is-available" : "is-empty"}`}
                key={route.id}
                onClick={() => onNavigate(route.id)}
              >
                <span className="route-card-topline">
                  <span className="route-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className={`route-status ${route.available ? "available" : "empty"}`}>
                    {route.available ? "可用" : "待开发"}
                  </span>
                </span>
                <span className="route-icon"><Icon size={25} /></span>
                <span className="route-card-title">{route.label}</span>
                <span className="route-card-table mono">{route.table}</span>
                <span className="route-card-description">{route.description}</span>
                <span className="route-card-action">
                  {route.available ? "进入查询" : "查看页面"}
                  <ArrowRight size={16} />
                </span>
              </button>
            );
          })}
        </section>
      </main>
    </div>
  );
}

function EmptyRoutePage({ route, onHome }: { route: RouteDefinition; onHome: () => void }) {
  const Icon = route.icon;
  return (
    <div className="app-shell">
      <Topbar statusText="本地测试环境" onHome={onHome} />
      <main className="workspace route-workspace">
        <button className="back-home-button" onClick={onHome}>
          <ArrowLeft size={16} />
          返回首页
        </button>
        <section className="empty-route-panel">
          <div className="empty-route-icon"><Icon size={28} /></div>
          <p className="eyebrow">ROUTE NOT BUILT</p>
          <h2>{route.table}</h2>
          <p>该业务路线页面暂为空，后续将在独立页面中开发。</p>
          <span className="empty-route-status">待开发</span>
        </section>
      </main>
    </div>
  );
}

function LatestPricesPage({ onHome }: { onHome: () => void }) {
  const [query, setQuery] = useState(DEFAULT_OPTIONS.query);
  const [limit, setLimit] = useState(DEFAULT_OPTIONS.limit);
  const [provider, setProvider] = useState("");
  const [useEmbedding, setUseEmbedding] = useState(true);
  const [useCandidateLlm, setUseCandidateLlm] = useState(true);
  const [stages, setStages] = useState<Record<string, StageEvent>>({});
  const [result, setResult] = useState<SearchResult | null>(null);
  const [selectedStage, setSelectedStage] = useState("query_understanding");
  const [running, setRunning] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const activeStage = stages[selectedStage];
  const summary = useMemo(() => selectedSummary(result), [result]);
  const completedCount = Object.values(stages).filter((stage) => stage.status === "completed").length;
  const errorCount = Object.values(stages).filter((stage) => stage.status === "error").length;

  /** 最新价格页面只维护自己的状态，切换到其他页面时整个组件会被卸载。 */
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
    setUseEmbedding(true);
    setUseCandidateLlm(true);
    setStages({});
    setResult(null);
    setRequestError(null);
  };

  return (
    <div className="app-shell">
      <Topbar
        statusText={running ? "查询进行中" : "最新价格查询"}
        stageCount={`${completedCount}/${STAGE_DEFINITIONS.length} stages`}
        onHome={onHome}
      />
      <main className="workspace route-workspace">
        <div className="route-breadcrumb">
          <button className="back-home-button" onClick={onHome}>
            <ArrowLeft size={16} />
            返回首页
          </button>
          <span className="breadcrumb-divider">/</span>
          <span>latest_prices</span>
        </div>

        <section className="query-band">
          <div className="section-heading">
            <div>
              <p className="eyebrow">LATEST PRICES ROUTE</p>
              <h2>最新价格查询</h2>
            </div>
            <div className="query-stats">
              <span><Server size={14} /> SSE trace</span>
              <span><Database size={14} /> latest_prices</span>
            </div>
          </div>
          <div className="query-form">
            <label className="query-input-wrap">
              <span>自然语言输入</span>
              <div className="query-input-line">
                <Search size={17} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => { if (event.key === "Enter") void runQuery(); }}
                  placeholder="例如：查询 EURUSD 的最新价格"
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
              <input value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="可选，例如 LSEG" />
            </label>
            <button className="primary-button" onClick={() => void runQuery()} disabled={running || !query.trim()} title="运行最新价格查询">
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
            <div className="route-metric">
              <span>测试范围</span>
              <strong>{result?.query_intent?.route ?? "-"}</strong>
            </div>
            <div><span>完成阶段</span><strong>{completedCount}</strong></div>
            <div><span>阶段错误</span><strong className={errorCount ? "danger-text" : ""}>{errorCount}</strong></div>
            <div><span>候选数量</span><strong>{result?.candidates?.length ?? "-"}</strong></div>
            <div><span>数据集候选</span><strong>{result?.dataset_search?.candidates?.length ?? "-"}</strong></div>
            <div><span>价格行</span><strong>{result?.price_result?.row_count ?? "-"}</strong></div>
          </div>
        </section>

        <div className="trace-grid">
          <section className="trace-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">PIPELINE TRACE</p>
                <h2>最新价格链路</h2>
              </div>
              <span className="panel-count">{Object.keys(stages).length} events</span>
            </div>
            <div className="stage-list">
              {STAGE_DEFINITIONS.map((definition, index) => {
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
                <h2>{STAGE_DEFINITIONS.find((stage) => stage.id === selectedStage)?.label ?? selectedStage}</h2>
              </div>
              <div className="detail-status">
                {stageIcon(activeStage?.status)} {statusLabel(activeStage?.status)}
              </div>
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
            <div>
              <p className="eyebrow">CANDIDATES</p>
              <h2>候选工具</h2>
            </div>
            <span className="panel-count">RRF → master → LLM</span>
          </div>
          <div className="candidate-table-wrap">
            <table>
              <thead>
                <tr><th>canonical_symbol</th><th>instrument_id</th><th>name</th><th>status</th><th>matched_by</th><th>rrf_score</th></tr>
              </thead>
              <tbody>
                {result?.candidates?.length ? result.candidates.map((candidate, index) => (
                  <tr key={`${String(candidate.canonical_symbol)}-${index}`}>
                    <td className="mono strong-cell">{String(candidate.canonical_symbol ?? "-")}</td>
                    <td className="mono">{String(candidate.instrument_id ?? "-")}</td>
                    <td>{String(candidate.master_name ?? candidate.name ?? "-")}</td>
                    <td><span className={`status-pill ${String(candidate.status ?? "unknown").toLowerCase()}`}>{String(candidate.status ?? "unknown")}</span></td>
                    <td className="method-list">{Array.isArray(candidate.matched_by) ? candidate.matched_by.join(" · ") : "-"}</td>
                    <td className="mono">{String(candidate.rrf_score ?? "-")}</td>
                  </tr>
                )) : <tr><td colSpan={6} className="empty-state">运行查询后显示候选</td></tr>}
              </tbody>
            </table>
          </div>
          {result?.warnings?.map((warning) => <div className="warning-line" key={warning}><TriangleAlert size={15} /> {warning}</div>)}
        </section>

        <section className="candidate-panel dataset-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">DATASET CANDIDATES</p>
              <h2>候选数据集</h2>
            </div>
            <span className="panel-count">RRF → source.dataset_catalog → LLM</span>
          </div>
          <div className="candidate-table-wrap">
            <table>
              <thead>
                <tr><th>dataset_id</th><th>provider</th><th>data_category</th><th>storage_table_name</th><th>status</th><th>matched_by</th><th>rrf_score</th></tr>
              </thead>
              <tbody>
                {result?.dataset_search?.candidates?.length ? result.dataset_search.candidates.map((candidate, index) => (
                  <tr key={`${String(candidate.dataset_id)}-${index}`}>
                    <td className="mono strong-cell">{String(candidate.dataset_id ?? "-")}</td>
                    <td className="mono">{String(candidate.provider ?? "-")}</td>
                    <td>{String(candidate.data_category ?? "-")}</td>
                    <td className="mono">{String(candidate.storage_table_name ?? "-")}</td>
                    <td><span className={`status-pill ${String(candidate.resolution_status ?? "unknown").toLowerCase()}`}>{String(candidate.resolution_status ?? "unknown")}</span></td>
                    <td className="method-list">{Array.isArray(candidate.matched_by) ? candidate.matched_by.join(" · ") : "-"}</td>
                    <td className="mono">{String(candidate.rrf_score ?? "-")}</td>
                  </tr>
                )) : <tr><td colSpan={7} className="empty-state">工具和供应商标识确认后显示数据集候选</td></tr>}
              </tbody>
            </table>
          </div>
          {result?.dataset_search?.warnings?.map((warning) => <div className="warning-line" key={warning}><TriangleAlert size={15} /> {warning}</div>)}
          {result?.dataset_resolution?.status === "resolved" && (
            <div className="dataset-resolution-line">
              <Database size={15} />
              <span>已确认：<strong className="mono">{result.dataset_resolution.dataset_id}</strong> → <strong className="mono">{result.dataset_resolution.storage_table_name}</strong></span>
            </div>
          )}
        </section>

        <section className="candidate-panel price-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">LATEST PRICE RESULT</p>
              <h2>最新价格结果</h2>
            </div>
            <span className={`status-pill ${String(result?.price_result?.status ?? "pending").toLowerCase()}`}>
              {result?.price_result?.status ?? "等待查询"}
            </span>
          </div>
          {result?.price_result?.status === "resolved" && result.price_result.rows[0] ? (
            <>
              <div className="price-context">
                <span className="mono">{result.price_result.instrument_id ?? "-"}</span>
                <span>{result.price_result.provider ?? "-"} / {result.price_result.identifier ?? "-"}</span>
                <span className="mono">{result.price_result.storage_table_name ?? "-"}</span>
              </div>
              <div className="price-field-grid">
                {(result.price_result.fields ?? []).map((field) => {
                  const fieldName = String(field.field_name ?? "");
                  const value = result.price_result?.rows[0]?.[fieldName];
                  return (
                    <div className="price-field" key={fieldName}>
                      <span className="mono">{fieldName}</span>
                      <strong>{String(value ?? "-")}</strong>
                      <small>{String(field.business_name ?? "")}{field.unit ? ` · ${String(field.unit)}` : ""}</small>
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="price-empty-state">
              {result?.price_result?.reason ?? "完成前置目录和字段确认后显示最新价格"}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function App() {
  const [route, setRoute] = useState<RouteId>(() => routeFromHash());

  useEffect(() => {
    const handleHashChange = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const onNavigate = (nextRoute: RouteId) => {
    navigateTo(nextRoute);
    setRoute(nextRoute);
  };

  if (route === "home") return <HomePage onNavigate={onNavigate} />;
  if (route === "unified-search") return <UnifiedSearchPage onHome={() => onNavigate("home")} />;

  const routeDefinition = ROUTE_DEFINITIONS.find((definition) => definition.id === route);
  if (!routeDefinition) return <HomePage onNavigate={onNavigate} />;
  if (route === "latest-prices") return <LatestPricesPage onHome={() => onNavigate("home")} />;
  if (route === "macro-observations") return <MacroObservationsPage onHome={() => onNavigate("home")} />;
  if (route === "market-bars") return <MarketBarsPage onHome={() => onNavigate("home")} />;
  if (route === "news-articles") return <NewsArticlesPage onHome={() => onNavigate("home")} />;
  return <EmptyRoutePage route={routeDefinition} onHome={() => onNavigate("home")} />;
}

export default App;
