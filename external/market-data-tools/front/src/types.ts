export type StageStatus = "running" | "completed" | "error" | "skipped";

/** 后端与前端共同使用的四条独立业务路线枚举值。 */
export type SearchRoute =
  | "latest_prices"
  | "macro_observations"
  | "market_bars"
  | "news_articles";

export interface StageEvent {
  stage: string;
  status: StageStatus;
  input: Record<string, unknown> | null;
  output: unknown;
  duration_ms: number | null;
  error: string | null;
}

/** 数据集目录最终确认对象；物理表名必须来自 source.dataset_catalog。 */
export interface DatasetResolution {
  status: string;
  dataset_id: string | null;
  storage_table_name: string | null;
  provider?: string | null;
  data_category?: string | null;
  dataset_name?: string | null;
  reason?: string;
}

/** latest_prices 路线中的独立数据集检索结果。 */
export interface DatasetSearchResult {
  query: string;
  candidate_selection_query?: string | null;
  candidate_selection_context?: Record<string, unknown>;
  provider_requested: string | null;
  methods: Record<string, number>;
  warnings: string[];
  catalog_resolution: Record<string, number>;
  candidates: Array<Record<string, unknown>>;
  model_selection: Record<string, unknown> | null;
  consistency_check?: {
    status: string;
    code?: string | null;
    selected_dataset_id?: string | null;
    candidate_dataset_ids?: string[];
    reason?: string;
  } | null;
  dataset_resolution: DatasetResolution;
}

/** dataset_field_catalog 解析后的受控字段计划。 */
export interface FieldResolution {
  status: string;
  dataset_id: string | null;
  storage_table_name: string | null;
  requested_fields: string[];
  fields: Array<Record<string, unknown>>;
  available_fields: string[];
  missing_catalog_fields: string[];
  missing_physical_columns: string[];
  reason: string;
}

/** source.latest_prices 适配器返回的最新价格结果。 */
export interface PriceResult {
  status: string;
  instrument_id?: string;
  provider?: string;
  identifier?: string;
  dataset_id?: string;
  storage_table_name?: string;
  filters?: Record<string, unknown>;
  fields?: Array<Record<string, unknown>>;
  rows: Array<Record<string, unknown>>;
  row_count?: number;
  reason?: string;
}

export interface SearchOptions {
  query: string;
  route: SearchRoute;
  limit: number;
  provider: string | null;
  use_embedding: boolean;
  use_candidate_llm: boolean;
  row_limit?: number;
  start_date?: string | null;
  end_date?: string | null;
}

/** 统一自然语言入口的请求参数；路线由后端意图识别，不由前端指定。 */
export type UnifiedSearchOptions = Omit<SearchOptions, "route">;

/** market_bars 路线解析后的日期和日线约束。 */
export interface MarketBarRequest {
  status: string;
  frequency: string | null;
  period_type: string;
  start_date: string | null;
  end_date: string | null;
  row_limit: number;
  reason: string;
}

/** source.market_bars 适配器返回的日线 OHLCV 结果。 */
export interface MarketBarsResult {
  status: string;
  instrument_id?: string;
  provider?: string;
  identifier?: string;
  dataset_id?: string;
  storage_table_name?: string;
  frequency?: string;
  start_date?: string;
  end_date?: string;
  fields?: Array<Record<string, unknown>>;
  rows: Array<Record<string, unknown>>;
  row_count?: number;
  reason?: string;
}

/** macro_observations 路线解析后的发布时间、频率和返回行数约束。 */
export interface MacroObservationRequest {
  status: string;
  period_type: string;
  start_date: string | null;
  end_date: string | null;
  frequency: string | null;
  row_limit: number;
  requested_fields: string[];
  reason: string;
}

/** source.macro_observations 适配器返回的宏观指标结果。 */
export interface MacroObservationRow {
  /** 只包含 dataset_field_catalog 确认的宏观业务值字段。 */
  data: Record<string, unknown>;
  /** 包含指标身份、发布时间、来源和单位等记录上下文。 */
  metadata: Record<string, unknown>;
}

export interface MacroObservationsResult {
  status: string;
  instrument_id?: string;
  provider?: string;
  identifier?: string;
  dataset_id?: string;
  storage_table_name?: string;
  frequency?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  filters?: Record<string, unknown>;
  fields?: Array<Record<string, unknown>>;
  rows: MacroObservationRow[];
  row_count?: number;
  reason?: string;
}

/** 新闻 AI 四路召回后的候选结果；新闻候选不做单篇大模型强制选择。 */
export interface NewsSearchResult {
  query: string;
  provider?: string | null;
  methods: Record<string, number>;
  warnings: string[];
  candidates: Array<Record<string, unknown>>;
  candidate_selection: Record<string, unknown> | null;
}

/** source.news_articles 回查后的业务字段和新闻记录元数据。 */
export interface NewsArticleRow {
  data: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface NewsArticlesResult {
  status: string;
  dataset_id?: string;
  storage_table_name?: string;
  fields?: Array<Record<string, unknown>>;
  filters?: Record<string, unknown>;
  rows: NewsArticleRow[];
  row_count?: number;
  reason?: string;
}

export interface SearchResult {
  /** 顶层查询状态；统一入口用它表示整体成功或拒绝，业务状态位于 execution。 */
  status?: string;
  query: string;
  /** 解析后的文本分流结果；query 字段仍保留用户完整原问题。 */
  instrument_query?: string | null;
  /** 查询理解模型生成的英文召回短语；它不代表正式 instrument_id。 */
  instrument_search_query?: string | null;
  dataset_query?: string | null;
  route?: SearchRoute;
  query_intent?: {
    route: SearchRoute;
    confidence: number;
    reason: string;
    instrument_text?: string | null;
    /** 多语言检索辅助文本，前端用于展示模型输出但不作为正式主数据标识。 */
    instrument_search_text?: string | null;
    provider_text?: string | null;
    time_expression?: string | null;
    request_text?: string | null;
  } | null;
  route_guard?: {
    accepted: boolean;
    requested_route: SearchRoute;
    recognized_route: SearchRoute;
    reason: string;
  } | null;
  methods: Record<string, number>;
  warnings: string[];
  master_resolution: Record<string, number>;
  candidates: Array<Record<string, unknown>>;
  model_selection: Record<string, unknown> | null;
  identifier_resolution: Record<string, unknown> | null;
  dataset_search: DatasetSearchResult | null;
  dataset_resolution: DatasetResolution | null;
  field_resolution: FieldResolution | null;
  price_result: PriceResult | null;
  market_bar_request?: MarketBarRequest | null;
  market_bars_result?: MarketBarsResult | null;
  macro_observation_request?: MacroObservationRequest | null;
  macro_observations_result?: MacroObservationsResult | null;
  news_query?: string | null;
  news_search?: NewsSearchResult | null;
  news_result?: NewsArticlesResult | null;
}

/** 统一入口在独立路线结果外补充的路由分发信息。 */
export interface UnifiedSearchResult extends SearchResult {
  interface: "unified_search";
  query_understanding?: Record<string, unknown> | null;
  dataset_consistency_check?: Record<string, unknown> | null;
  execution?: Record<string, unknown> | null;
  adapter?: string | null;
  routing: {
    mode: "dataset_catalog";
    dataset_id: string | null;
    storage_table_name: string | null;
    adapter: string | null;
    reason: string;
  };
}

/**
 * 正式服务接口的精简响应。
 *
 * 四条业务路线共享这个外层协议；不同路线只改变 data 数组中每条业务记录的
 * 字段，不把数据集候选、字段目录或模型判断等内部过程暴露给正式调用方。
 */
export interface PublicSearchResponse {
  status: "success" | "rejected" | "error" | string;
  data: Array<Record<string, unknown>>;
  code?: string;
  message?: string;
}

export type ServerEvent =
  | { type: "stage"; payload: StageEvent }
  | { type: "result"; payload: SearchResult | UnifiedSearchResult }
  | { type: "error"; payload: { message: string; error_type: string } }
  | { type: "done"; payload: { finished_at: string } };
