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
  Newspaper,
  Play,
  RotateCcw,
  Search,
  Server,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import { formatJson, streamSearch } from "./api";
import type { SearchResult, ServerEvent, StageEvent } from "./types";

interface NewsArticlesPageProps {
  onHome: () => void;
}

const NEWS_STAGES = [
  { id: "query_understanding", label: "查询理解和新闻主体提取", category: "对话大模型" },
  { id: "dataset_exact_match", label: "数据集精确匹配", category: "检索" },
  { id: "dataset_keyword_search", label: "数据集关键词检索", category: "检索" },
  { id: "dataset_pg_trgm_search", label: "数据集 pg_trgm 模糊检索", category: "检索" },
  { id: "dataset_embedding_search", label: "数据集 Embedding 语义检索", category: "模型" },
  { id: "dataset_rrf_merge", label: "数据集 RRF 合并", category: "程序" },
  { id: "dataset_catalog", label: "dataset_catalog 正式回查", category: "数据库" },
  { id: "dataset_candidate_selector", label: "数据集候选大模型筛选", category: "模型" },
  { id: "dataset_consistency_check", label: "数据集一致性校验", category: "程序" },
  { id: "compatibility_route_check", label: "独立页面范围校验", category: "程序" },
  { id: "news_date_request", label: "新闻日期解析", category: "程序" },
  { id: "dataset_field_catalog", label: "dataset_field_catalog 字段解析", category: "数据库" },
  { id: "news_exact_match", label: "新闻精确匹配", category: "检索" },
  { id: "news_keyword_search", label: "新闻关键词检索", category: "检索" },
  { id: "news_pg_trgm_search", label: "新闻 pg_trgm 模糊检索", category: "检索" },
  { id: "news_embedding_search", label: "新闻 Embedding 语义检索", category: "模型" },
  { id: "news_rrf_merge", label: "新闻 RRF 合并", category: "程序" },
  { id: "news_articles_query", label: "source.news_articles 回查", category: "业务表" },
] as const;

const DEFAULT_QUERY = "查询 EURUSD 的相关新闻";

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

function textPreview(value: unknown, length = 260): string {
  if (value == null) return "";
  // 新闻正文可能包含 HTML；这里仅做展示层的短摘要，不改变后端返回的源表内容。
  const plain = String(value).replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  return plain.length > length ? `${plain.slice(0, length)}...` : plain;
}

function NewsArticlesPage({ onHome }: NewsArticlesPageProps) {
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [limit, setLimit] = useState(3);
  const [provider, setProvider] = useState("");
  const [useEmbedding, setUseEmbedding] = useState(true);
  const [useCandidateLlm, setUseCandidateLlm] = useState(true);
  const [stages, setStages] = useState<Record<string, StageEvent>>({});
  const [result, setResult] = useState<SearchResult | null>(null);
  const [selectedStage, setSelectedStage] = useState("query_understanding");
  const [running, setRunning] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const activeStage = stages[selectedStage];
  const completedCount = Object.values(stages).filter((stage) => stage.status === "completed").length;
  const errorCount = Object.values(stages).filter((stage) => stage.status === "error").length;
  const candidates = result?.news_search?.candidates ?? [];
  const rows = result?.news_result?.rows ?? [];
  const firstRow = rows[0]?.data ?? {};
  const summary = useMemo(() => {
    if (result?.news_result?.status === "resolved" && rows.length > 0) {
      return {
        value: String(firstRow.title ?? "新闻候选已返回"),
        meta: `${String(rows[0]?.metadata?.source ?? "")} · ${String(rows[0]?.metadata?.publish_time ?? "")} · ${rows.length} 条`,
      };
    }
    return {
      value: result?.news_result?.status === "skipped" ? "新闻查询已停止" : "等待新闻候选",
      meta: result?.news_result?.reason ?? "执行查询后显示与 EUR/USD 文本或语义相关的新闻",
    };
  }, [firstRow.title, result?.news_result, rows]);

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
        route: "news_articles",
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
    setQuery(DEFAULT_QUERY);
    setLimit(3);
    setProvider("");
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
          <span>{running ? "查询进行中" : "新闻资讯查询"}</span>
          <span className="status-divider" />
          <span className="mono">{completedCount}/{NEWS_STAGES.length} stages</span>
        </div>
      </header>

      <main className="workspace route-workspace">
        <div className="route-breadcrumb">
          <button className="back-home-button" onClick={onHome}><ArrowLeft size={16} /> 返回首页</button>
          <span className="breadcrumb-divider">/</span>
          <span>news_articles</span>
        </div>

        <section className="query-band">
          <div className="section-heading">
            <div>
              <p className="eyebrow">NEWS ARTICLES ROUTE</p>
              <h2>新闻资讯查询</h2>
            </div>
            <div className="query-stats">
              <span><Server size={14} /> SSE trace</span>
              <span><Database size={14} /> news_articles</span>
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
                  placeholder="例如：查询 EURUSD 的相关新闻"
                  spellCheck={false}
                />
              </div>
            </label>
            <label className="compact-field">
              <span>目录候选上限</span>
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
            <button className="primary-button" onClick={() => void runQuery()} disabled={running || !query.trim()} title="运行新闻查询">
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
              <span>新闻 Embedding 语义检索</span>
            </label>
            <label className="toggle-control">
              <input type="checkbox" checked={useCandidateLlm} onChange={(event) => setUseCandidateLlm(event.target.checked)} />
              <span className="toggle-visual" />
              <span>数据集候选大模型</span>
            </label>
            {requestError && <span className="inline-error"><AlertCircle size={15} /> {requestError}</span>}
          </div>
        </section>

        <section className="result-strip">
          <div className="result-main news-result-main">
            <div className="result-icon"><Sparkles size={18} /></div>
            <div>
              <p className="eyebrow">NEWS CANDIDATES</p>
              <h2>{summary.value}</h2>
              <p>{summary.meta}</p>
            </div>
          </div>
          <div className="result-metrics">
            <div className="route-metric"><span>测试范围</span><strong>{result?.query_intent?.route ?? "-"}</strong></div>
            <div><span>候选数量</span><strong>{candidates.length || "-"}</strong></div>
            <div><span>返回新闻</span><strong>{result?.news_result?.row_count ?? "-"}</strong></div>
            <div><span>完成阶段</span><strong>{completedCount}</strong></div>
            <div><span>阶段错误</span><strong className={errorCount ? "danger-text" : ""}>{errorCount}</strong></div>
          </div>
        </section>

        <div className="trace-grid">
          <section className="trace-panel">
            <div className="panel-heading">
              <div><p className="eyebrow">PIPELINE TRACE</p><h2>新闻检索链路</h2></div>
              <span className="panel-count">{Object.keys(stages).length} events</span>
            </div>
            <div className="stage-list">
              {NEWS_STAGES.map((definition, index) => {
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
                <h2>{NEWS_STAGES.find((stage) => stage.id === selectedStage)?.label ?? selectedStage}</h2>
              </div>
              <div className="detail-status">{stageIcon(activeStage?.status)} {statusLabel(activeStage?.status)}</div>
            </div>
            <div className="detail-meta">
              <span><Clock3 size={14} /> {activeStage?.duration_ms != null ? `${activeStage.duration_ms} ms` : "未运行"}</span>
              <span><FileJson size={14} /> input / output</span>
            </div>
            <div className="json-columns">
              <div className="json-block"><div className="json-label">INPUT</div><pre>{formatJson(activeStage?.input)}</pre></div>
              <div className="json-block output-block"><div className="json-label">OUTPUT</div><pre>{formatJson(activeStage?.output)}</pre></div>
            </div>
            {activeStage?.error && <div className="error-box"><TriangleAlert size={16} /><span>{activeStage.error}</span></div>}
          </section>
        </div>

        <section className="candidate-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">NEWS CANDIDATES</p><h2>文本或语义相关候选</h2></div>
            <span className="panel-count">exact · keyword · pg_trgm · embedding · RRF</span>
          </div>
          <div className="candidate-table-wrap">
            <table>
              <thead><tr><th>article_id</th><th>source</th><th>publish_time</th><th>title</th><th>matched_by</th><th>rrf_score</th></tr></thead>
              <tbody>
                {candidates.length ? candidates.map((candidate, index) => (
                  <tr key={`${String(candidate.article_id)}-${index}`}>
                    <td className="mono strong-cell">{String(candidate.article_id ?? "-")}</td>
                    <td className="mono">{String(candidate.source ?? "-")}</td>
                    <td className="mono">{String(candidate.publish_time ?? "-")}</td>
                    <td>{String(candidate.title ?? "-")}</td>
                    <td className="method-list">{Array.isArray(candidate.matched_by) ? candidate.matched_by.join(" · ") : "-"}</td>
                    <td className="mono">{String(candidate.rrf_score ?? "-")}</td>
                  </tr>
                )) : <tr><td colSpan={6} className="empty-state">运行查询后显示新闻候选</td></tr>}
              </tbody>
            </table>
          </div>
          {result?.news_search?.warnings?.map((warning) => <div className="warning-line" key={warning}><TriangleAlert size={15} /> {warning}</div>)}
        </section>

        <section className="candidate-panel news-results-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">SOURCE RESULTS</p><h2>source.news_articles 返回</h2></div>
            <span className="panel-count">TITLE · SUMMARY · CONTENT</span>
          </div>
          {rows.length ? (
            <div className="news-result-list">
              {rows.map((row, index) => (
                <article className="news-result-card" key={`${String(row.metadata.article_id)}-${index}`}>
                  <div className="news-result-card-heading">
                    <h3>{String(row.data.title ?? "无标题")}</h3>
                    <span className="mono">{String(row.metadata.publish_time ?? "")}</span>
                  </div>
                  <div className="news-result-meta">
                    <span>{String(row.metadata.source ?? "")}</span>
                    <span>{String(row.metadata.article_id ?? "")}</span>
                    <span>matched: {Array.isArray(row.metadata.matched_by) ? row.metadata.matched_by.join(" · ") : "-"}</span>
                  </div>
                  <p>{textPreview(row.data.summary || row.data.content, 420) || "该文章没有摘要或正文预览"}</p>
                </article>
              ))}
            </div>
          ) : (
            <div className="price-empty-state">{result?.news_result?.reason ?? "完成目录和字段确认后显示新闻候选"}</div>
          )}
        </section>
      </main>
    </div>
  );
}

export { NewsArticlesPage };
