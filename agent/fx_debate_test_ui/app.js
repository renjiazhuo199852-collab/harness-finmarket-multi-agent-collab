const $ = (id) => document.getElementById(id);

const elements = {
  databaseStatus: $("database-status"),
  llmStatus: $("llm-status"),
  runStatus: $("run-status"),
  elapsed: $("elapsed"),
  themeToggle: $("theme-toggle"),
  form: $("run-form"),
  symbol: $("symbol"),
  selectedSymbolLabel: $("selected-symbol-label"),
  workspaceSymbolTitle: $("workspace-symbol-title"),
  runButton: $("run-button"),
  confirmCost: $("confirm-cost"),
  formMessage: $("form-message"),
  jobId: $("job-id"),
  swarmId: $("swarm-id"),
  eventCount: $("event-count"),
  consoleMessage: $("console-message"),
  activeCalls: $("active-calls"),
  activeCount: $("active-count"),
  eventFeed: $("event-feed"),
  chatThread: $("chat-thread"),
  detailEmpty: $("detail-empty"),
  eventDetail: $("event-detail"),
  detailMeta: $("detail-meta"),
  artifactCards: $("artifact-cards"),
  detailInput: $("detail-input"),
  detailInputView: $("detail-input-view"),
  detailOutput: $("detail-output"),
  detailOutputView: $("detail-output-view"),
  detailJson: $("detail-json"),
  diagnosticsPanel: $("diagnostics-panel"),
  diagnosticsSummary: $("diagnostics-summary"),
  diagnosticsList: $("diagnostics-list"),
  copyDiagnostics: $("copy-diagnostics"),
  chatView: $("chat-view"),
  chatPageStatus: $("chat-page-status"),
  chatSuggestions: $("chat-suggestions"),
  eventPane: document.querySelector(".event-pane"),
  detailPane: document.querySelector(".detail-pane"),
  centerSplitter: document.querySelector('.pane-splitter[data-resize="center"]'),
  copyDetail: $("copy-detail"),
  agentList: $("agent-list"),
  resultSummary: $("result-summary"),
  decision: $("decision"),
  confidence: $("confidence"),
  showReport: $("show-report"),
  reportDialog: $("report-dialog"),
  reportContent: $("report-content"),
  closeReport: $("close-report"),
  filters: $("filters"),
  viewTabs: $("view-tabs"),
  canvasView: $("canvas-view"),
  dataView: $("data-view"),
  reportView: $("report-view"),
  debateCanvas: $("debate-canvas"),
  canvasProgress: $("canvas-progress"),
  dataHealthGrid: $("data-health-grid"),
  dataPreviewMeta: $("data-preview-meta"),
  dataPreviewDomain: $("data-preview-domain"),
  dataPreviewTabs: $("data-preview-tabs"),
  dataPreviewTableWrap: $("data-preview-table-wrap"),
  dataPreviewJson: $("data-preview-json"),
  contextBadge: $("context-badge"),
  contextJson: $("context-json"),
  copyContext: $("copy-context"),
  copyReport: $("copy-report"),
  reportViewContent: $("report-view-content"),
  reportRawContent: $("report-raw-content"),
  reportDialogReadable: $("report-dialog-readable"),
  reportDecision: $("report-decision"),
  reportConfidence: $("report-confidence"),
  reportContext: $("report-context"),
  settingsView: $("settings-view"),
  apiSettingsForm: $("api-settings-form"),
  dataSettingsForm: $("data-settings-form"),
  settingProvider: $("setting-provider"),
  settingModel: $("setting-model"),
  settingBaseUrl: $("setting-base-url"),
  settingApiKey: $("setting-api-key"),
  settingReasoning: $("setting-reasoning"),
  settingDataSource: $("setting-data-source"),
  settingExcelPath: $("setting-excel-path"),
  settingDbHost: $("setting-db-host"),
  settingDbPort: $("setting-db-port"),
  settingDbName: $("setting-db-name"),
  settingDbUser: $("setting-db-user"),
  settingDbPassword: $("setting-db-password"),
  apiSettingsStatus: $("api-settings-status"),
  dataSettingsStatus: $("data-settings-status"),
  useSyntheticData: $("use-synthetic-data"),
  settingsNote: $("settings-note"),
  conversationList: $("conversation-list"),
  newConversation: $("new-conversation"),
  chatForm: $("chat-form"),
  chatInput: $("chat-input"),
  chatSend: $("chat-send"),
  stats: {
    agent: $("stat-agent"),
    tool: $("stat-tool"),
    sdk: $("stat-sdk"),
    database: $("stat-database"),
    failed: $("stat-failed"),
  },
};

const AGENTS = [
  { id: "pair_bull", name: "Pair Bull", dependency: "第一层 · 独立分析" },
  { id: "pair_bear", name: "Pair Bear", dependency: "第一层 · 独立分析" },
  {
    id: "macro_technical",
    name: "Macro + Technical",
    dependency: "第一层 · 独立分析",
  },
  {
    id: "fx_risk_officer",
    name: "FX Risk Officer",
    dependency: "等待三份 AgentArgument",
  },
  {
    id: "debate_judge",
    name: "Debate Judge / FX PM",
    dependency: "等待 RiskReview",
  },
];

const state = {
  ready: false,
  running: false,
  jobId: null,
  startedAt: null,
  eventOffset: 0,
  events: [],
  visibleEvents: [],
  active: new Map(),
  agents: new Map(),
  filter: "all",
  selectedSequence: null,
  autoFollow: true,
  result: null,
  diagnostics: [],
  dataPreview: null,
  dataPreviewDomain: "all",
  goal: "",
  view: "chat",
  conversationId: null,
  conversations: [],
  jobTimer: null,
  eventTimer: null,
  clockTimer: null,
};

const THEME_STORAGE_KEY = "fx-debate-theme";

function applyTheme(theme, { persist = true } = {}) {
  const nextTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = nextTheme;
  document.documentElement.style.colorScheme = nextTheme;
  if (elements.themeToggle) {
    const lightMode = nextTheme === "light";
    elements.themeToggle.textContent = lightMode ? "☾" : "☀";
    elements.themeToggle.title = lightMode ? "切换到深色主题" : "切换到浅色主题";
    elements.themeToggle.setAttribute(
      "aria-label",
      lightMode ? "切换到深色主题" : "切换到浅色主题",
    );
  }
  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    } catch (_error) {
      // Theme switching still works for this page when storage is unavailable.
    }
  }
}

function initTheme() {
  let savedTheme = null;
  try {
    savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  } catch (_error) {
    // Fall back to the browser preference below.
  }
  const prefersLight = window.matchMedia?.("(prefers-color-scheme: light)").matches;
  applyTheme(
    savedTheme === "light" || savedTheme === "dark"
      ? savedTheme
      : prefersLight
        ? "light"
        : "dark",
    { persist: false },
  );
}

function resetAgents() {
  state.agents = new Map(
    AGENTS.map((agent) => [
      agent.id,
      { ...agent, status: "waiting", current: agent.dependency },
    ]),
  );
  renderAgents();
}

function setView(view) {
  state.view = view;
  for (const button of elements.viewTabs?.querySelectorAll("button") || []) {
    button.classList.toggle("active", button.dataset.view === view);
  }
  elements.canvasView.classList.toggle("hidden", view !== "canvas");
  elements.dataView.classList.toggle("hidden", view !== "data");
  elements.reportView.classList.toggle("hidden", view !== "report");
  elements.settingsView.classList.toggle("hidden", view !== "settings");
  elements.chatView.classList.toggle("hidden", view !== "chat");
  elements.eventPane.classList.toggle("hidden", view !== "logs");
  elements.detailPane.classList.toggle("hidden", view !== "logs");
  elements.centerSplitter?.classList.toggle("hidden", view !== "logs");
  elements.filters.classList.toggle("hidden", view !== "logs");
  elements.chatForm.classList.toggle("hidden", view !== "chat");
  if (view === "canvas") renderCanvas();
  if (view === "data") {
    renderDataHealth();
    renderDataPreview();
  }
  if (view === "report") renderReportPage();
  if (view === "logs") renderEvents();
  if (view === "logs") renderDiagnostics();
  if (view === "settings") loadSettings();
}

async function loadSettings() {
  try {
    const response = await fetch("/api/settings", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const llm = payload.llm || {};
    const data = payload.data || {};
    const database = data.database || {};
    elements.settingProvider.value = llm.provider || "";
    elements.settingModel.value = llm.model || "";
    elements.settingBaseUrl.value = llm.base_url || "";
    elements.settingApiKey.value = "";
    elements.settingReasoning.value = llm.reasoning_effort || "low";
    elements.settingDataSource.value = data.data_source || "database";
    elements.settingExcelPath.value = data.excel_path || "";
    elements.settingDbHost.value = database.host || "";
    elements.settingDbPort.value = database.port || "";
    elements.settingDbName.value = database.name || "";
    elements.settingDbUser.value = database.user || "";
    elements.settingDbPassword.value = "";
    elements.apiSettingsStatus.textContent = llm.api_key_configured ? `${llm.api_key_env} · 已配置` : "未配置密钥";
    elements.dataSettingsStatus.textContent = data.data_source === "excel" ? "Excel" : "Database";
    elements.useSyntheticData.disabled = !data.synthetic_path;
    elements.useSyntheticData.title = data.synthetic_path || "尚未生成合成数据";
    elements.settingsNote.textContent = data.synthetic_path
      ? `可用完整合成数据：${data.synthetic_path}`
      : "尚未生成完整合成数据，请运行生成脚本。";
  } catch (error) {
    elements.settingsNote.textContent = `设置读取失败：${error.message}`;
  }
}

async function saveSettings(payload, statusElement) {
  statusElement.textContent = "保存中…";
  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    statusElement.textContent = "已保存";
    elements.settingsNote.textContent = "设置已写入本机 .env；请重新检查运行环境后再启动。";
    await checkHealth();
  } catch (error) {
    statusElement.textContent = "保存失败";
    elements.settingsNote.textContent = error.message;
  }
}

function renderReportPage() {
  const result = state.result || {};
  const decision = result.decision?.decision || result.decision || "—";
  const confidence = Number(result.decision?.confidence);
  elements.reportDecision.textContent = decision;
  elements.reportConfidence.textContent = Number.isFinite(confidence)
    ? `${Math.round(confidence * 100)}%`
    : "—";
  elements.reportContext.textContent = result.evidence_context_id || "—";
  const markdown = result.report_markdown || "";
  if (elements.reportRawContent) elements.reportRawContent.textContent = markdown || "—";
  if (elements.reportViewContent) {
    elements.reportViewContent.replaceChildren();
    if (result.decision || markdown) {
      renderReadableReport(elements.reportViewContent, result);
    } else {
      const empty = document.createElement("div");
      empty.className = "report-empty";
      empty.textContent = "运行完成后显示结构化报告。";
      elements.reportViewContent.append(empty);
    }
  }
  if (elements.reportDialogReadable) {
    elements.reportDialogReadable.replaceChildren();
    if (result.decision || markdown) renderReadableReport(elements.reportDialogReadable, result);
  }
}

const REPORT_DECISION_LABELS = {
  long: "做多",
  short: "做空",
  wait: "观望",
  hedge: "对冲",
};

function renderReadableReport(parent, result) {
  const decision = result.decision && typeof result.decision === "object" ? result.decision : {};
  const symbol = decision.display_symbol || decision.canonical_symbol || currentSymbol();
  const action = REPORT_DECISION_LABELS[decision.decision] || decision.decision || "待定";
  const hero = document.createElement("section");
  hero.className = "report-hero";
  const heroTitle = document.createElement("div");
  heroTitle.className = "report-hero-title";
  const heading = document.createElement("h4");
  heading.textContent = `${symbol} · ${action}`;
  const subtitle = document.createElement("p");
  subtitle.textContent = decision.thesis || "已完成结构化审阅，详见下方各章节。";
  heroTitle.append(heading, subtitle);
  const badge = document.createElement("span");
  badge.className = `report-decision-badge ${decision.decision || "pending"}`;
  badge.textContent = action;
  hero.append(heroTitle, badge);
  parent.append(hero);

  const metrics = [
    ["置信度", Number.isFinite(Number(decision.confidence)) ? `${Math.round(Number(decision.confidence) * 100)}%` : "—"],
    ["判断期限", decision.horizon_days ? `${decision.horizon_days} 天` : "—"],
    ["数据截止", formatDateTime(decision.data_as_of || result.data_preview?.as_of)],
    ["数据来源", result.data_source_policy || "冻结证据包"],
  ];
  const metricGrid = document.createElement("div");
  metricGrid.className = "report-readable-grid";
  for (const [label, value] of metrics) appendReportMetric(metricGrid, label, value);
  parent.append(metricGrid);

  if (decision.thesis) appendReportCallout(parent, "核心判断", decision.thesis, "primary");
  appendReportProbabilities(parent, decision.scenario_probabilities);
  appendReportTradePlan(parent, decision);
  if (decision.risk_assessment) appendReportCallout(parent, "风险评估", decision.risk_assessment, "warning");
  appendReportListSection(parent, "关键证据", decision.key_evidence_ids, "evidence");
  appendReportListSection(parent, "已采用的分析结论", decision.adopted_claim_ids, "evidence");
  appendReportListSection(parent, "已排除的分析结论", decision.rejected_claim_ids, "muted");
  appendReportListSection(parent, "失效条件", decision.invalidation_conditions, "warning");
  appendReportListSection(parent, "仍缺数据", decision.missing_data, "warning");
  if (decision.next_review_trigger) appendReportCallout(parent, "下一次复核", decision.next_review_trigger, "muted");

  const sections = parseReadableMarkdown(result.report_markdown || "");
  const skip = new Set(["核心判断", "交易建议", "风险与证据", "失效与复核条件", "machine-readable v2"]);
  for (const section of sections) {
    if (skip.has(section.title.toLowerCase())) continue;
    appendReportMarkdownSection(parent, section);
  }
}

function appendReportMetric(parent, label, value) {
  const card = document.createElement("article");
  card.className = "report-readable-metric";
  const caption = document.createElement("span");
  caption.textContent = label;
  const content = document.createElement("strong");
  content.textContent = value || "—";
  card.append(caption, content);
  parent.append(card);
}

function appendReportCallout(parent, title, text, tone = "muted") {
  if (!text) return;
  const card = document.createElement("article");
  card.className = `report-callout ${tone}`;
  const heading = document.createElement("h5");
  heading.textContent = title;
  const body = document.createElement("p");
  body.textContent = cleanReportText(text);
  card.append(heading, body);
  parent.append(card);
}

function appendReportProbabilities(parent, values) {
  if (!values || typeof values !== "object") return;
  const card = document.createElement("section");
  card.className = "report-section report-probabilities";
  const heading = document.createElement("h5");
  heading.textContent = "情景概率";
  card.append(heading);
  const grid = document.createElement("div");
  grid.className = "report-probability-grid";
  for (const [key, label] of [["bull", "上涨"], ["base", "基准"], ["bear", "下跌"]]) {
    const value = Number(values[key]);
    const item = document.createElement("div");
    item.className = `report-probability ${key}`;
    const name = document.createElement("span");
    name.textContent = label;
    const number = document.createElement("strong");
    number.textContent = Number.isFinite(value) ? `${Math.round(value * 100)}%` : "—";
    item.append(name, number);
    grid.append(item);
  }
  card.append(grid);
  parent.append(card);
}

function appendReportTradePlan(parent, decision) {
  const trade = decision.trade_plan;
  const card = document.createElement("section");
  card.className = "report-section report-trade-plan";
  const heading = document.createElement("h5");
  heading.textContent = "执行计划";
  const body = document.createElement("p");
  if (decision.decision === "wait" || !trade) {
    body.textContent = "观望：暂不建立方向性仓位，等待关键证据或失效条件重新评估。";
  } else {
    const entry = Array.isArray(trade.entry_zone) ? trade.entry_zone.join(" – ") : "按触发条件";
    const stop = trade.stop_loss == null ? "未设置" : String(trade.stop_loss);
    const targets = Array.isArray(trade.targets) && trade.targets.length ? trade.targets.join("、") : "未设置";
    body.textContent = `方向：${REPORT_DECISION_LABELS[decision.decision] || decision.decision} · 入场区间：${entry} · 止损：${stop} · 目标：${targets}`;
  }
  card.append(heading, body);
  parent.append(card);
}

function appendReportListSection(parent, title, values, tone = "muted") {
  if (!Array.isArray(values) || !values.length) return;
  const card = document.createElement("section");
  card.className = `report-section report-list-section ${tone}`;
  const heading = document.createElement("h5");
  heading.textContent = title;
  const list = document.createElement("ul");
  for (const value of values) {
    const item = document.createElement("li");
    item.textContent = cleanReportText(value);
    list.append(item);
  }
  card.append(heading, list);
  parent.append(card);
}

function parseReadableMarkdown(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const sections = [];
  let current = null;
  let inCode = false;
  for (const line of lines) {
    if (/^```/.test(line.trim())) {
      inCode = !inCode;
      continue;
    }
    if (inCode) continue;
    const heading = line.match(/^#{1,4}\s+(.+)$/);
    if (heading) {
      current = { title: cleanReportText(heading[1]), lines: [] };
      sections.push(current);
    } else if (current && line.trim()) {
      current.lines.push(line.trim());
    }
  }
  return sections;
}

function appendReportMarkdownSection(parent, section) {
  if (!section.lines.length) return;
  const card = document.createElement("section");
  card.className = "report-section report-markdown-section";
  const heading = document.createElement("h5");
  heading.textContent = section.title;
  card.append(heading);
  const tableLines = section.lines.filter((line) => line.startsWith("|"));
  if (tableLines.length >= 2) {
    const table = document.createElement("table");
    table.className = "report-readable-table";
    const rows = tableLines.filter((line) => !/^\|\s*[-:| ]+\|$/.test(line));
    rows.forEach((line, index) => {
      const row = document.createElement(index === 0 ? "thead" : "tbody");
      const tr = document.createElement("tr");
      line.split("|").slice(1, -1).forEach((cellText) => {
        const cell = document.createElement(index === 0 ? "th" : "td");
        cell.textContent = cleanReportText(cellText.trim());
        tr.append(cell);
      });
      row.append(tr);
      table.append(row);
    });
    card.append(table);
  }
  const list = document.createElement("ul");
  let hasList = false;
  for (const line of section.lines) {
    if (line.startsWith("|")) continue;
    if (/^[-*]\s+/.test(line)) {
      const item = document.createElement("li");
      item.textContent = cleanReportText(line.replace(/^[-*]\s+/, ""));
      list.append(item);
      hasList = true;
    } else {
      const paragraph = document.createElement("p");
      paragraph.textContent = cleanReportText(line);
      card.append(paragraph);
    }
  }
  if (hasList) card.append(list);
  parent.append(card);
}

function cleanReportText(value) {
  return String(value ?? "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`{1,3}/g, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*(.*?)\*/g, "$1")
    .trim();
}

function renderCanvas() {
  if (!elements.debateCanvas) return;
  const view = window.FxRunView.projectFxRun(state.events, state.result);
  elements.canvasProgress.textContent = `${view.completed} / ${view.total} 完成 · 第 ${view.currentStage} 阶段`;
  elements.debateCanvas.replaceChildren();
  const columns = [
    view.agents.filter((agent) => agent.stage === 1),
    view.agents.filter((agent) => agent.stage === 2),
    view.agents.filter((agent) => agent.stage === 3),
  ];
  columns.forEach((column, index) => {
    const lane = document.createElement("div");
    lane.className = `canvas-lane stage-${index + 1}`;
    const title = document.createElement("div");
    title.className = "lane-title";
    title.textContent = index === 0 ? "并行分析" : index === 1 ? "风险复核" : "最终决策";
    lane.append(title);
    for (const agent of column) {
      const node = document.createElement("button");
      node.type = "button";
      node.className = `flow-node ${agent.status}`;
      node.dataset.agentId = agent.id;
      node.innerHTML = `<span class="node-status-dot"></span><span class="node-copy"><strong>${agent.label}</strong><small>${agent.operation}</small><em>${agent.eventCount} 个事件</em></span><span class="node-chevron">›</span>`;
      node.addEventListener("click", () => {
        const event = [...state.visibleEvents].reverse().find((item) => item.agent_id === agent.id) || state.events.find((item) => item.agent_id === agent.id);
        if (event) {
          state.selectedSequence = event.sequence;
          state.autoFollow = false;
          setView("logs");
          renderEvents();
          showDetail(event);
        }
      });
      lane.append(node);
    }
    elements.debateCanvas.append(lane);
    if (index < columns.length - 1) {
      const arrow = document.createElement("div");
      arrow.className = "canvas-arrow";
      arrow.textContent = "→";
      elements.debateCanvas.append(arrow);
    }
  });
}

function renderDataHealth() {
  if (!elements.dataHealthGrid) return;
  const manifest = state.dataPreview?.manifest || {};
  const types = [
    ["quote", "报价", "latest_prices 快照"],
    ["market", "市场 / K 线", "market_bars 与技术指标"],
    ["macro", "宏观数据", `${currentSymbol()} 两端相对宏观 scorecard`],
    ["news", "新闻聚类", "标题、标签和时间窗口去重"],
  ];
  const cards = types.map(([key, label, hint]) => {
    const related = state.events.filter((event) => JSON.stringify(event).toLowerCase().includes(key));
    const domainManifest = manifest[key] || (key === "quote" ? manifest.quote : null);
    const done = Boolean(domainManifest && domainManifest.status !== "insufficient_evidence");
    const status = domainManifest?.status || (related.length ? "已读取" : "等待/未知");
    const count = domainManifest?.record_count ?? related.length;
    const missing = domainManifest?.missing_fields?.length
      ? ` · 缺少 ${domainManifest.missing_fields.join(", ")}`
      : "";
    return `<article class="health-card ${done ? "good" : "pending"}"><div class="health-card-head"><strong>${label}</strong><span>${escapeHtml(status)}</span></div><p>${hint}</p><small>${count} 条记录${escapeHtml(missing)}</small></article>`;
  });
  elements.dataHealthGrid.innerHTML = cards.join("");
  const contextEvent = state.events.find((event) => event.type === "context_ready");
  elements.contextJson.textContent = pretty(
    state.dataPreview || contextEvent?.data || { status: "等待运行" },
  );
}

function renderDataPreview() {
  if (!elements.dataPreviewTableWrap) return;
  const preview = state.dataPreview;
  const domain = state.dataPreviewDomain || "all";
  if (!preview?.domains) {
    elements.dataPreviewMeta.textContent = "运行后显示本轮 Evidence Bundle 的限量明细。";
    elements.dataPreviewTableWrap.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "data-preview-empty";
    empty.textContent = "运行后可在这里查看实际数据行。";
    elements.dataPreviewTableWrap.append(empty);
    elements.dataPreviewJson.textContent = "—";
    return;
  }

  const source = preview.source || "unknown";
  const asOf = preview.as_of ? formatDateTime(preview.as_of) : "—";
  const contextId = preview.evidence_context_id || "—";
  const marketDomain = preview.domains.market || {};
  const marketSourceCount = marketDomain.source_row_count || 0;
  const marketShown = marketDomain.source_row_shown || 0;
  elements.dataPreviewMeta.textContent = `${source} · as_of ${asOf} · Context ${contextId} · 报价/市场已展示 ${marketShown}/${marketSourceCount} 条原始行，表格按域限量展示`;
  elements.dataPreviewJson.textContent = pretty({
    manifest: preview.manifest,
    derived: preview.derived,
    counts: preview.counts,
    raw_counts: preview.raw_counts,
  });
  for (const button of elements.dataPreviewTabs?.querySelectorAll("button") || []) {
    button.classList.toggle("active", button.dataset.previewDomain === domain);
  }
  if (elements.dataPreviewDomain) elements.dataPreviewDomain.value = domain;

  const rowsFor = (name, data) => [
    ...(data.rows || []),
    ...(data.source_rows || []).map((row) => ({ ...row, _source_preview: true })),
  ].map((row) => ({ ...row, _domain: name }));
  const selected = domain === "all"
    ? Object.entries(preview.domains).flatMap(([name, data]) => rowsFor(name, data))
    : rowsFor(domain, preview.domains[domain] || {});
  elements.dataPreviewTableWrap.replaceChildren();
  if (!selected.length) {
    const empty = document.createElement("div");
    empty.className = "data-preview-empty";
    empty.textContent = domain === "all" ? "本轮没有可预览的证据行。" : "该数据域没有可预览记录。";
    elements.dataPreviewTableWrap.append(empty);
    return;
  }

  const table = document.createElement("table");
  table.className = "data-preview-table";
  const columns = [
    ["_domain", "数据域"],
    ["name", "名称"],
    ["timeframe", "周期"],
    ["value", "值"],
    ["observation_time", "观测时间"],
    ["quality_status", "质量"],
    ["source_table", "来源表"],
    ["evidence_id", "Evidence ID"],
  ];
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const [, label] of columns) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);
  table.append(head);
  const body = document.createElement("tbody");
  for (const row of selected) {
    const tableRow = document.createElement("tr");
    for (const [key] of columns) {
      const cell = document.createElement("td");
      const value = row[key];
      cell.textContent = key === "_domain"
        ? previewDomainLabel(value)
        : key === "value"
          ? previewValue(value)
          : key.endsWith("_time")
            ? formatDateTime(value)
          : String(value ?? "—");
      if (key === "quality_status") cell.className = `preview-quality ${String(value || "")}`;
      if (key === "evidence_id") cell.className = "preview-evidence-id";
      tableRow.append(cell);
    }
    body.append(tableRow);
  }
  table.append(body);
  elements.dataPreviewTableWrap.append(table);
}

function previewDomainLabel(domain) {
  return {
    market: "报价 / 市场",
    technical: "技术指标",
    macro: "宏观数据",
    news: "新闻事件",
  }[domain] || domain || "—";
}

function previewValue(value) {
  if (value === undefined || value === null) return "—";
  if (typeof value === "object") {
    const quoteKeys = ["last", "bid", "ask", "mid"];
    const barKeys = ["open", "high", "low", "close"];
    const keys = Object.keys(value);
    if (quoteKeys.some((key) => key in value)) {
      return quoteKeys.filter((key) => key in value).map((key) => `${key.toUpperCase()} ${formatNumber(value[key])}`).join(" · ");
    }
    if (barKeys.some((key) => key in value)) {
      return barKeys.filter((key) => key in value).map((key) => `${key.toUpperCase()} ${formatNumber(value[key])}`).join(" · ");
    }
    if ("country" in value && "actual" in value) {
      return `${value.country || "—"} · 实际 ${formatNumber(value.actual)} · 预测 ${formatNumber(value.forecast)}`;
    }
    if ("title" in value) return String(value.title || "—");
    return keys.map((key) => `${key}: ${humanValue(value[key])}`).join(" · ");
  }
  return String(value);
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("en-US", { maximumFractionDigits: 6 }) : String(value);
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.toLocaleDateString("zh-CN")} ${date.toLocaleTimeString("zh-CN", { hour12: false })}`;
}

function renderChat() {
  if (!elements.chatThread) return;
  elements.chatThread.replaceChildren();
  const firstInput = state.events.find((event) => event.type === "worker_started")?.data?.input || {};
  const goal = state.goal || firstInput.goal || firstInput.user_prompt || "";
  if (goal) {
    const user = document.createElement("div");
    user.className = "chat-bubble user";
    user.innerHTML = `<span class="chat-role">你</span><p></p>`;
    user.querySelector("p").textContent = goal;
    elements.chatThread.append(user);
  }
  const assistant = document.createElement("div");
  assistant.className = "chat-bubble assistant";
  const symbol = currentSymbol();
  const status = state.running ? "正在读取 Evidence Context，并调度五个 Agent…" : state.result ? "分析已完成，下面是可回放的事件与结构化结果。" : `你好，我可以帮你分析 ${symbol}。请在下方输入研究目标。`;
  assistant.innerHTML = `<span class="chat-role">FX Debate</span><p></p>`;
  assistant.querySelector("p").textContent = status;
  elements.chatThread.append(assistant);
  if (elements.chatPageStatus) {
    elements.chatPageStatus.textContent = state.running
      ? "分析进行中"
      : state.result
        ? "本轮已完成"
        : "等待请求";
    elements.chatPageStatus.className = `chat-page-status ${state.running ? "running" : state.result ? "completed" : ""}`;
  }
  elements.chatThread.scrollTop = elements.chatThread.scrollHeight;
}

async function loadConversations() {
  if (!elements.conversationList) return;
  try {
    const response = await fetch("/api/conversations", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.conversations = await response.json();
    renderConversations();
  } catch (_error) {
    elements.conversationList.innerHTML = '<div class="history-empty">历史服务暂不可用</div>';
  }
}

function renderConversations() {
  elements.conversationList.replaceChildren();
  if (!state.conversations.length) {
    elements.conversationList.innerHTML = '<div class="history-empty">运行一次后会自动保存</div>';
    return;
  }
  for (const conversation of state.conversations) {
    const entry = document.createElement("div");
    entry.className = "history-entry";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `history-item ${conversation.conversation_id === state.conversationId ? "active" : ""}`;
    button.dataset.conversationId = conversation.conversation_id || "";
    button.dataset.jobId = conversation.last_job_id || "";
    const updated = conversation.updated_at ? formatTime(conversation.updated_at) : "";
    button.innerHTML = `<strong></strong><span></span>`;
    button.querySelector("strong").textContent = conversation.title || "FX Debate";
    button.querySelector("span").textContent = `${conversation.last_status || "queued"} · ${(conversation.jobs || []).length} 次运行${updated ? ` · ${updated}` : ""}`;
    button.onclick = () => {
      elements.consoleMessage.textContent = "正在打开历史会话…";
      resumeConversation(conversation);
    };
    entry.append(button);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "history-delete";
    remove.textContent = "×";
    remove.title = conversation.last_status === "running" || conversation.last_status === "queued"
      ? "运行中的会话不能删除"
      : "删除会话及其运行记录";
    remove.setAttribute("aria-label", `删除 ${conversation.title || "FX Debate"}`);
    remove.disabled = conversation.last_status === "running" || conversation.last_status === "queued";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteConversation(conversation);
    });
    entry.append(remove);
    elements.conversationList.append(entry);
  }
}

async function deleteConversation(conversation) {
  const conversationId = conversation?.conversation_id;
  if (!conversationId) return;
  const title = conversation.title || "FX Debate";
  if (!window.confirm(`删除“${title}”及其全部运行记录？此操作不可恢复。`)) return;

  const removeButton = [...elements.conversationList.querySelectorAll(".history-delete")]
    .find((button) => button.getAttribute("aria-label") === `删除 ${title}`);
  if (removeButton) removeButton.disabled = true;
  try {
    const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`, {
      method: "DELETE",
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);

    if (conversationId === state.conversationId) {
      elements.newConversation?.click();
      elements.consoleMessage.textContent = "当前会话已删除，已切换到新会话。";
    } else {
      elements.consoleMessage.textContent = `已删除会话“${title}”。`;
    }
    await loadConversations();
  } catch (error) {
    if (removeButton) removeButton.disabled = false;
    elements.consoleMessage.textContent = `删除会话失败：${error.message}`;
  }
}

async function resumeConversation(conversation) {
  const jobId = conversation.last_job_id;
  if (!jobId) return;
  const titlePair = String(conversation.title || "").match(/[A-Z]{3}\s*\/\s*[A-Z]{3}/i)?.[0];
  if (titlePair && elements.symbol) {
    const normalized = titlePair.replaceAll(" ", "").toUpperCase();
    const option = [...elements.symbol.options].find((item) => item.value === normalized);
    if (option) elements.symbol.value = option.value;
    updateSymbolChrome();
  }
  state.conversationId = conversation.conversation_id;
  renderConversations();
  prepareNewRun();
  state.jobId = jobId;
  state.running = conversation.last_status === "queued" || conversation.last_status === "running";
  elements.jobId.textContent = jobId;
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(jobId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error("历史运行不存在");
    const job = await response.json();
    state.startedAt = Date.parse(job.started_at || job.created_at) || Date.now();
    await pollEvents();
    const input = state.events.find((event) => event.type === "worker_started")?.data?.input || {};
    state.goal = input.goal || input.user_prompt || "";
    renderChat();
    if (job.status === "completed") {
      state.result = job.result;
      showResult(job.result);
      finishRun("completed");
    } else if (job.status === "failed") {
      finishRun("failed", job.error || "历史运行失败");
    } else {
      startPolling();
    }
  } catch (error) {
    finishRun("failed", error.message);
  }
}

function renderAgents() {
  elements.agentList.replaceChildren();
  for (const agent of state.agents.values()) {
    const card = document.createElement("article");
    card.className = `agent-card ${agent.status}`;

    const head = document.createElement("div");
    head.className = "agent-card-head";
    const name = document.createElement("strong");
    name.textContent = agent.name;
    const status = document.createElement("span");
    status.className = "agent-state";
    status.textContent = agent.status;
    head.append(name, status);

    const current = document.createElement("p");
    current.textContent = agent.current;
    card.append(head, current);
    elements.agentList.append(card);
  }
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const health = await response.json();
    state.ready = Boolean(health.ready);
    setChip(
      elements.databaseStatus,
      health.data.ready
        ? `数据源 · ${health.data.source}`
        : `数据源未就绪 · ${health.data.source}`,
      health.data.ready ? "ready" : "failed",
    );
    const llmText = health.llm.ready
      ? `${health.llm.provider} · ${health.llm.model}`
      : "LLM 未配置";
    setChip(elements.llmStatus, llmText, health.llm.ready ? "ready" : "failed");
    elements.formMessage.textContent = state.ready
      ? "环境已就绪。勾选确认后可启动。"
      : "Excel/Database 数据源与 LLM 必须同时配置完成。";
    elements.formMessage.classList.toggle("error", !state.ready);
  } catch (error) {
    state.ready = false;
    setChip(elements.databaseStatus, "服务不可用", "failed");
    setChip(elements.llmStatus, "服务不可用", "failed");
    elements.formMessage.textContent = `健康检查失败：${error.message}`;
    elements.formMessage.classList.add("error");
  }
  updateRunButton();
}

function setChip(element, text, status) {
  element.textContent = text;
  element.className = `status-chip ${status}`;
}

function updateRunButton() {
  elements.runButton.disabled =
    !state.ready || state.running || !elements.confirmCost.checked;
}

function buildRequest() {
  const asOf = $("as-of").value;
  return {
    target: currentSymbol(),
    horizon_count: Number($("horizon-count").value),
    horizon_unit: $("horizon-unit").value,
    timeframe: $("timeframe").value,
    risk_profile: $("risk-profile").value,
    request_id: $("request-id").value.trim() || null,
    conversation_id: state.conversationId,
    goal: elements.chatInput?.value.trim() || null,
    as_of: asOf ? new Date(asOf).toISOString() : null,
    confirm_cost: true,
  };
}

function currentSymbol() {
  return elements.symbol?.value || "EUR/USD";
}

function updateSymbolChrome() {
  const symbol = currentSymbol();
  if (elements.selectedSymbolLabel) elements.selectedSymbolLabel.textContent = symbol;
  if (elements.workspaceSymbolTitle) elements.workspaceSymbolTitle.textContent = `${symbol} 分析`;
  document.title = `${symbol} Debate 工作台`;
  const placeholder = `例如：分析 ${symbol} 未来两周走势，重点关注利率差、宏观数据和 4H/1D 技术状态`;
  if (elements.chatInput && !elements.chatInput.value) elements.chatInput.placeholder = placeholder;
  for (const button of elements.chatSuggestions?.querySelectorAll("[data-prompt-template]") || []) {
    button.dataset.prompt = button.dataset.promptTemplate.replaceAll("{symbol}", symbol);
  }
}

async function startRun(event) {
  event.preventDefault();
  if (!state.ready || state.running || !elements.confirmCost.checked) return;

  const chatGoal = elements.chatInput?.value.trim();
  if (chatGoal) elements.consoleMessage.textContent = chatGoal;

  prepareNewRun();
  state.goal = chatGoal || `分析 ${currentSymbol()} 的当前市场状态并给出可审计的研究结论。`;
  renderChat();
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequest()),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    state.jobId = payload.job_id;
    state.conversationId = payload.conversation_id || state.conversationId;
    state.startedAt = Date.now();
    elements.jobId.textContent = state.jobId;
    localStorage.setItem("fx-debate-job-id", state.jobId);
    startPolling();
    loadConversations();
  } catch (error) {
    finishRun("failed", error.message);
  }
}

function prepareNewRun() {
  stopPolling();
  state.running = true;
  state.jobId = null;
  state.startedAt = Date.now();
  state.eventOffset = 0;
  state.events = [];
  state.visibleEvents = [];
  state.active.clear();
  state.filter = "all";
  state.selectedSequence = null;
  state.autoFollow = true;
  state.result = null;
  state.diagnostics = [];
  state.dataPreview = null;
  state.dataPreviewDomain = "all";
  state.goal = "";
  elements.contextBadge.textContent = "Evidence Context 构建中";
  elements.contextBadge.classList.remove("ready");
  resetAgents();
  elements.eventFeed.textContent = "等待第一条事件…";
  elements.eventFeed.className = "event-feed empty";
  elements.activeCalls.textContent = "等待调用…";
  elements.activeCalls.className = "active-calls empty";
  elements.eventCount.textContent = "0";
  elements.jobId.textContent = "创建中";
  elements.swarmId.textContent = "—";
  elements.resultSummary.classList.add("hidden");
  hideDetail();
  elements.formMessage.textContent = "真实 Debate 已启动，请勿关闭服务。";
  elements.formMessage.classList.remove("error");
  elements.consoleMessage.textContent = "正在等待 Swarm 运行事件。";
  renderDiagnostics();
  renderChat();
  setChip(elements.runStatus, "运行中", "running");
  updateRunButton();
}

function startPolling() {
  stopPolling();
  pollEvents();
  pollJob();
  state.eventTimer = window.setInterval(pollEvents, 800);
  state.jobTimer = window.setInterval(pollJob, 1500);
  state.clockTimer = window.setInterval(updateClock, 1000);
  updateClock();
}

function stopPolling() {
  for (const timer of [state.eventTimer, state.jobTimer, state.clockTimer]) {
    if (timer) window.clearInterval(timer);
  }
  state.eventTimer = null;
  state.jobTimer = null;
  state.clockTimer = null;
}

async function pollEvents() {
  if (!state.jobId) return;
  try {
    const response = await fetch(
      `/api/runs/${encodeURIComponent(state.jobId)}/events?after=${state.eventOffset}`,
      { cache: "no-store" },
    );
    if (!response.ok) return;
    const payload = await response.json();
    state.eventOffset = payload.next_after;
    if (!payload.events.length) return;
    for (const event of payload.events) ingestEvent(event);
    elements.eventCount.textContent = String(state.eventOffset);
    renderEvents();
    state.diagnostics = window.FxRunView.buildDiagnostics(state.events);
    renderDiagnostics();
    renderActiveCalls();
    renderAgents();
    renderStats();
    renderCanvas();
    renderDataHealth();
    renderChat();
  } catch (_error) {
    // The next interval retries. Job status remains the authoritative failure path.
  }
}

function renderDiagnostics() {
  if (!elements.diagnosticsPanel || !elements.diagnosticsList) return;
  const diagnostics = state.diagnostics || [];
  elements.diagnosticsPanel.classList.toggle("hidden", diagnostics.length === 0);
  if (!diagnostics.length) {
    elements.diagnosticsList.replaceChildren();
    return;
  }
  elements.diagnosticsSummary.textContent = `${diagnostics.length} 个可回溯故障；点击“查看关联日志”定位原始 Tool 输入/输出。`;
  elements.diagnosticsList.replaceChildren();
  for (const diagnostic of diagnostics) {
    const card = document.createElement("article");
    card.className = "diagnostic-card";
    const head = document.createElement("div");
    head.className = "diagnostic-card-head";
    const title = document.createElement("strong");
    title.textContent = `${diagnostic.title} · ${agentName(diagnostic.agent_id)}`;
    const meta = document.createElement("span");
    meta.textContent = `#${diagnostic.sequence ?? "—"} · ${formatTime(diagnostic.timestamp)} · ${diagnostic.phase || "unknown"}`;
    head.append(title, meta);
    const message = document.createElement("p");
    message.className = "diagnostic-message";
    message.textContent = diagnostic.message || "未提供错误描述。";
    card.append(head, message);
    const errors = diagnostic.validation_errors || [];
    if (errors.length) {
      const table = document.createElement("table");
      table.className = "diagnostic-errors";
      table.innerHTML = "<thead><tr><th>代码</th><th>字段路径</th><th>具体原因</th></tr></thead>";
      const body = document.createElement("tbody");
      for (const issue of errors.slice(0, 8)) {
        const row = document.createElement("tr");
        for (const key of ["code", "path", "message"]) {
          const cell = document.createElement("td");
          cell.textContent = issue?.[key] || "—";
          row.append(cell);
        }
        body.append(row);
      }
      table.append(body);
      card.append(table);
    }
    const footer = document.createElement("div");
    footer.className = "diagnostic-card-foot";
    const trace = document.createElement("span");
    trace.textContent = `关联事件 ${(
      diagnostic.related_sequences || [diagnostic.sequence]
    ).join(", ")} · 重试 ${diagnostic.retry_count || 0} 次`;
    const open = document.createElement("button");
    open.type = "button";
    open.className = "text-button";
    open.textContent = "查看关联日志";
    open.addEventListener("click", () => {
      const sequence = (diagnostic.related_sequences || []).at(-1) || diagnostic.sequence;
      const event = state.events.find((item) => item.sequence === sequence) ||
        state.events.find((item) => item.sequence === diagnostic.sequence);
      if (event) {
        state.selectedSequence = event.sequence;
        state.autoFollow = false;
        renderEvents();
        showDetail(event);
      }
    });
    footer.append(trace, open);
    card.append(footer);
    elements.diagnosticsList.append(card);
  }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(state.jobId)}`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const job = await response.json();
    if (job.result?.run_id) elements.swarmId.textContent = job.result.run_id;
    if (job.status === "completed") {
      await pollEvents();
      state.result = job.result;
      showResult(job.result);
      finishRun("completed");
    } else if (job.status === "failed") {
      await pollEvents();
      state.result = job.result;
      finishRun("failed", job.error || "Debate 运行失败");
    }
  } catch (error) {
    elements.consoleMessage.textContent = `状态轮询异常，将自动重试：${error.message}`;
  }
}

function ingestEvent(event) {
  state.events.push(event);
  const info = eventInfo(event);
  event._view = info;

  const preview = event.data?.data_preview;
  if (preview && typeof preview === "object") {
    state.dataPreview = preview;
    if (preview.evidence_context_id) {
      elements.contextBadge.textContent = preview.evidence_context_id;
      elements.contextBadge.classList.add("ready");
    }
    renderDataPreview();
  }

  updateActive(event, info);
  updateAgent(event, info);
  if (!isNoiseEvent(event.type)) {
    state.visibleEvents.push(event);
    if (state.autoFollow) state.selectedSequence = event.sequence;
  }
}

function eventInfo(event) {
  const type = String(event.type || "unknown");
  const data = event.data || {};
  let layer = "system";
  if (type.startsWith("worker_") || type.startsWith("agent_") || type.startsWith("task_")) {
    layer = "agent";
  } else if (type.startsWith("tool_")) {
    layer = "tool";
  } else if (type.startsWith("sdk_")) {
    layer = "sdk";
  } else if (type.startsWith("database_")) {
    layer = "database";
  }

  let status = "info";
  if (
    type.endsWith("_started") ||
    type === "tool_call" ||
    type === "worker_started"
  ) {
    status = "running";
  } else if (
    type.endsWith("_completed") ||
    type === "tool_result" ||
    type === "worker_completed"
  ) {
    status = data.status === "error" ? "failed" : "completed";
  } else if (
    type.includes("failed") ||
    type.includes("error") ||
    type.includes("timeout") ||
    type.includes("incomplete")
  ) {
    status = "failed";
  }

  const operation =
    data.operation ||
    data.tool ||
    humanizeType(type);
  return {
    layer,
    status,
    operation,
    actor: agentName(event.agent_id),
    duration: Number.isFinite(data.elapsed_ms) ? `${data.elapsed_ms} ms` : "—",
  };
}

function humanizeType(type) {
  return type.replaceAll("_", " ");
}

function agentName(agentId) {
  return AGENTS.find((agent) => agent.id === agentId)?.name || agentId || "Swarm";
}

function eventKey(event, info) {
  const data = event.data || {};
  if (info.layer === "agent") {
    return `agent:${event.agent_id || ""}:${event.task_id || ""}`;
  }
  if (info.layer === "tool") {
    return `tool:${data.call_id || data.parent_call_id || event.sequence}`;
  }
  return `${info.layer}:${data.call_id || event.sequence}`;
}

function completionKey(event, info) {
  const data = event.data || {};
  if (info.layer === "agent") {
    return `agent:${event.agent_id || ""}:${event.task_id || ""}`;
  }
  if (info.layer === "tool") return `tool:${data.call_id || ""}`;
  return `${info.layer}:${data.call_id || ""}`;
}

function updateActive(event, info) {
  if (!["agent", "tool", "sdk", "database"].includes(info.layer)) return;
  if (info.status === "running") {
    state.active.set(eventKey(event, info), {
      layer: info.layer,
      actor: info.actor,
      operation: info.operation,
      started: event.timestamp,
    });
  } else if (["completed", "failed"].includes(info.status)) {
    state.active.delete(completionKey(event, info));
  }
}

function updateAgent(event, info) {
  const agent = state.agents.get(event.agent_id);
  if (!agent) return;
  const type = event.type;
  if (type === "worker_started") {
    agent.status = "running";
    agent.current = "准备模型输入";
  } else if (type === "worker_completed") {
    agent.status = "completed";
    agent.current = "输出已完成";
  } else if (
    type.includes("failed") ||
    type.includes("timeout") ||
    type.includes("incomplete")
  ) {
    agent.status = "failed";
    agent.current = event.data?.error || humanizeType(type);
  } else if (type === "tool_call") {
    agent.status = "running";
    agent.current = `Tool · ${info.operation}`;
  } else if (type.startsWith("sdk_") && info.status === "running") {
    agent.current = `SDK · ${info.operation}`;
  } else if (type.startsWith("database_") && info.status === "running") {
    agent.current = `PostgreSQL · ${info.operation}`;
  } else if (type === "worker_text") {
    agent.status = "running";
    agent.current = "LLM 正在生成";
  } else if (type === "task_heartbeat") {
    const phase = event.data?.phase;
    agent.current = phase === "tool" ? "Tool 仍在运行" : "LLM 仍在生成";
  }
}

function isNoiseEvent(type) {
  return type === "worker_text" || type === "task_heartbeat";
}

function renderEvents() {
  const filtered = state.visibleEvents.filter(
    (event) => state.filter === "all" || event._view.layer === state.filter,
  );
  elements.eventFeed.replaceChildren();
  elements.eventFeed.className = "event-feed";

  if (!filtered.length) {
    elements.eventFeed.textContent = "当前筛选下暂无事件";
    elements.eventFeed.classList.add("empty");
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const event of filtered) {
    const info = event._view;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "event-row";
    if (event.sequence === state.selectedSequence) row.classList.add("selected");
    row.addEventListener("click", () => {
      state.selectedSequence = event.sequence;
      state.autoFollow = false;
      renderEvents();
      showDetail(event);
    });

    row.append(
      textCell(formatTime(event.timestamp), "event-time"),
      tagCell(info.layer, `layer-tag layer-${info.layer}`),
      operationCell(info.operation, info.actor),
      tagCell(info.status, `state-tag state-${info.status}`),
      textCell(info.duration, "event-duration"),
    );
    fragment.append(row);
  }
  elements.eventFeed.append(fragment);

  const selected = state.visibleEvents.find(
    (event) => event.sequence === state.selectedSequence,
  );
  if (selected && state.autoFollow) {
    showDetail(selected);
    elements.eventFeed.scrollTop = elements.eventFeed.scrollHeight;
  }
}

function textCell(text, className) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  return span;
}

function tagCell(text, className) {
  const wrapper = document.createElement("span");
  const tag = document.createElement("span");
  tag.className = className;
  tag.textContent = text;
  wrapper.append(tag);
  return wrapper;
}

function operationCell(operation, actor) {
  const wrapper = document.createElement("span");
  wrapper.className = "event-operation";
  const strong = document.createElement("strong");
  strong.textContent = operation;
  const small = document.createElement("small");
  small.textContent = actor;
  wrapper.append(strong, small);
  return wrapper;
}

function renderActiveCalls() {
  elements.activeCount.textContent = String(state.active.size);
  elements.activeCalls.replaceChildren();
  if (!state.active.size) {
    elements.activeCalls.textContent = "当前没有活动调用";
    elements.activeCalls.className = "active-calls empty";
    return;
  }
  elements.activeCalls.className = "active-calls";
  for (const item of state.active.values()) {
    const card = document.createElement("div");
    card.className = "active-call";
    const title = document.createElement("strong");
    title.textContent = `${item.layer.toUpperCase()} · ${item.operation}`;
    const meta = document.createElement("span");
    meta.textContent = `${item.actor} · ${formatTime(item.started)}`;
    card.append(title, meta);
    elements.activeCalls.append(card);
  }
}

function renderStats() {
  const counts = { agent: 0, tool: 0, sdk: 0, database: 0, failed: 0 };
  for (const event of state.visibleEvents) {
    const { layer, status } = event._view;
    if (
      ["agent", "tool", "sdk", "database"].includes(layer) &&
      event._view.status === "running"
    ) {
      counts[layer] += 1;
    }
    if (status === "failed") counts.failed += 1;
  }
  for (const key of Object.keys(counts)) {
    elements.stats[key].textContent = String(counts[key]);
  }
}

function showDetail(event) {
  const data = event.data || {};
  elements.detailEmpty.classList.add("hidden");
  elements.eventDetail.classList.remove("hidden");
  elements.detailMeta.textContent = [
    `#${event.sequence}`,
    event._view.layer,
    event._view.status,
    event.agent_id || "swarm",
    event.task_id || "—",
    event.timestamp,
  ].join(" · ");
  const cards = window.FxRunView.artifactCards(event);
  elements.artifactCards.replaceChildren();
  for (const cardData of cards) {
    const card = document.createElement("div");
    card.className = `artifact-card ${cardData.tone}`;
    card.innerHTML = `<span>${cardData.label}</span><strong>${cardData.value}</strong>`;
    elements.artifactCards.append(card);
  }
  const input = data.input ?? data.arguments ?? { operation: data.operation, tool: data.tool };
  elements.detailInput.textContent = pretty(input);
  renderStructuredInput(input, event);
  const output =
    data.output ??
    data.result_preview ??
    data.error ??
    {
      status: data.status,
      elapsed_ms: data.elapsed_ms,
      iteration: data.iteration,
    };
  elements.detailOutput.textContent = pretty(output);
  renderStructuredOutput(output);
  elements.detailJson.textContent = pretty(event);
}

function renderStructuredOutput(value) {
  if (!elements.detailOutputView) return;
  let parsed = value;
  if (typeof parsed === "string") {
    try { parsed = JSON.parse(parsed); } catch (_error) { parsed = null; }
  }
  elements.detailOutputView.replaceChildren();
  if (!parsed || typeof parsed !== "object") {
    renderHumanText(elements.detailOutputView, String(value ?? "—"));
    return;
  }
  if (Array.isArray(parsed.errors)) {
    const header = document.createElement("div");
    header.className = `validation-banner ${parsed.valid ? "valid" : "invalid"}`;
    header.innerHTML = `<strong>${parsed.valid ? "验证通过" : "验证失败"}</strong><span>${escapeHtml(parsed.mode || "contract")} · ${parsed.errors.length} 个错误</span>`;
    elements.detailOutputView.append(header);
    if (parsed.errors.length) {
      const table = document.createElement("table");
      table.className = "structured-table";
      table.innerHTML = "<thead><tr><th>代码</th><th>路径</th><th>原因</th></tr></thead>";
      const body = document.createElement("tbody");
      for (const issue of parsed.errors) {
        const row = document.createElement("tr");
        for (const key of ["code", "path", "message"]) {
          const cell = document.createElement("td");
          cell.textContent = issue?.[key] || "—";
          row.append(cell);
        }
        body.append(row);
      }
      table.append(body);
      elements.detailOutputView.append(table);
    }
    if (Array.isArray(parsed.checked_evidence_ids) && parsed.checked_evidence_ids.length) {
      const evidence = document.createElement("div");
      evidence.className = "evidence-chip-list";
      evidence.innerHTML = `<span>已核验证据</span>${parsed.checked_evidence_ids.map((id) => `<code>${escapeHtml(id)}</code>`).join("")}`;
      elements.detailOutputView.append(evidence);
    }
    return;
  }
  renderHumanObject(elements.detailOutputView, parsed);
}

const FIELD_LABELS = {
  evidence_context_id: "Evidence Context",
  agent_role: "执行角色",
  schema_version: "契约版本",
  hypothesis_direction: "假设方向",
  hypothesis_status: "假设状态",
  analysis_status: "分析状态",
  relative_macro_state: "相对宏观状态",
  technical_state: "技术状态",
  cross_confirmation: "交叉确认",
  reliability: "可靠性",
  risk_level: "风险等级",
  allowed_actions: "允许动作",
  decision: "最终决策",
  confidence: "置信度",
  horizon_days: "判断期限",
  summary: "摘要",
  thesis: "核心判断",
  risk_summary: "风险摘要",
  missing_data: "缺失数据",
  key_evidence_ids: "关键证据",
  checked_evidence_ids: "已核验证据",
  errors: "校验错误",
  warnings: "提示",
  status: "状态",
  ok: "结果",
  mode: "校验模式",
  tool_name: "工具",
  query_id: "查询编号",
  iterations: "迭代次数",
  elapsed_ms: "耗时",
};

function fieldLabel(key) {
  return FIELD_LABELS[key] || String(key).replaceAll("_", " ");
}

function parseDetailValue(value) {
  if (typeof value !== "string") return value;
  return window.FxRunView.extractJson(value) || value;
}

function appendInfoBlock(parent, label, value, className = "detail-info-block") {
  const block = document.createElement("div");
  block.className = className;
  const title = document.createElement("span");
  title.className = "detail-info-label";
  title.textContent = label;
  const content = document.createElement("div");
  content.className = "detail-info-value";
  if (typeof value === "string") {
    content.textContent = value;
  } else {
    content.textContent = humanValue(value);
  }
  block.append(title, content);
  parent.append(block);
  return block;
}

function humanValue(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (Array.isArray(value)) return value.map((item) => humanValue(item)).join("、");
  if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${fieldLabel(key)}：${humanValue(item)}`).join("；");
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return String(value);
}

function appendChips(parent, values, className = "evidence-chip-list") {
  const list = Array.isArray(values) ? values : values ? [values] : [];
  if (!list.length) return;
  const wrap = document.createElement("div");
  wrap.className = className;
  for (const item of list) {
    const chip = document.createElement("span");
    chip.className = "detail-chip";
    chip.textContent = humanValue(item);
    wrap.append(chip);
  }
  parent.append(wrap);
}

function renderHumanText(parent, text) {
  const normalized = String(text || "—").trim();
  if (!normalized) {
    const empty = document.createElement("p");
    empty.className = "structured-empty";
    empty.textContent = "暂无可展示内容";
    parent.append(empty);
    return;
  }
  const lines = normalized.split(/\r?\n/);
  const list = document.createElement("ul");
  list.className = "human-text-list";
  let inList = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (/^#{1,4}\s/.test(trimmed)) {
      const heading = document.createElement("h5");
      heading.textContent = trimmed.replace(/^#{1,4}\s+/, "");
      parent.append(heading);
    } else if (/^[-*]\s/.test(trimmed)) {
      const item = document.createElement("li");
      item.textContent = trimmed.replace(/^[-*]\s+/, "");
      list.append(item);
      inList = true;
    } else {
      if (inList) {
        parent.append(list.cloneNode(true));
        list.replaceChildren();
        inList = false;
      }
      const paragraph = document.createElement("p");
      paragraph.textContent = trimmed.replace(/\*\*/g, "");
      parent.append(paragraph);
    }
  }
  if (inList) parent.append(list);
}

function renderStructuredInput(value, event) {
  if (!elements.detailInputView) return;
  const parent = elements.detailInputView;
  parent.replaceChildren();
  const input = parseDetailValue(value);
  if (!input || typeof input !== "object") {
    renderHumanText(parent, String(value ?? "—"));
    return;
  }
  const data = event?.data || {};
  if (data.tool || data.arguments) {
    const tool = data.tool || input.tool || input.name || "工具调用";
    appendInfoBlock(parent, "正在调用", tool, "detail-call-title");
    const params = { ...input };
    delete params.run_dir;
    delete params.tool;
    delete params.name;
    const entries = Object.entries(params);
    if (entries.length) {
      const table = document.createElement("table");
      table.className = "structured-table key-value-table";
      table.innerHTML = "<thead><tr><th>参数</th><th>内容</th></tr></thead>";
      const body = document.createElement("tbody");
      for (const [key, item] of entries) {
        const row = document.createElement("tr");
        const keyCell = document.createElement("th");
        keyCell.textContent = fieldLabel(key);
        const valueCell = document.createElement("td");
        valueCell.textContent = humanValue(parseDetailValue(item));
        row.append(keyCell, valueCell);
        body.append(row);
      }
      table.append(body);
      parent.append(table);
    }
    return;
  }
  if (input.user_prompt || input.goal) {
    appendInfoBlock(parent, "用户任务", input.user_prompt || input.goal, "detail-info-block emphasis");
    if (input.upstream_summaries && typeof input.upstream_summaries === "object") {
      const names = Object.keys(input.upstream_summaries);
      appendInfoBlock(parent, "上游输入", `${names.length} 份分析已注入：${names.join("、")}`);
    }
    if (input.tools) appendChips(parent, input.tools, "detail-chip-list");
    if (input.system_prompt) {
      const details = document.createElement("details");
      details.className = "detail-secondary";
      const summary = document.createElement("summary");
      summary.textContent = "查看 Agent 职责说明";
      const content = document.createElement("div");
      content.className = "detail-long-text";
      renderHumanText(content, input.system_prompt);
      details.append(summary, content);
      parent.append(details);
    }
    return;
  }
  renderHumanObject(parent, input);
}

function renderHumanObject(parent, parsed) {
  if (parsed.valid !== undefined || parsed.status || parsed.ok !== undefined) {
    const status = parsed.valid !== undefined
      ? (parsed.valid ? "验证通过" : "验证失败")
      : parsed.ok === false || parsed.status === "error" ? "调用失败" : humanValue(parsed.status || "已完成");
    const banner = document.createElement("div");
    banner.className = `validation-banner ${parsed.valid === false || parsed.ok === false || parsed.status === "error" ? "invalid" : "valid"}`;
    banner.innerHTML = `<strong>${escapeHtml(status)}</strong><span>${escapeHtml(fieldLabel(parsed.mode || "result"))}</span>`;
    parent.append(banner);
  }
  const headlineKeys = ["agent_role", "hypothesis_direction", "hypothesis_status", "analysis_status", "relative_macro_state", "technical_state", "cross_confirmation", "reliability", "risk_level", "decision", "confidence", "horizon_days", "iterations", "elapsed_ms"];
  const headline = headlineKeys.filter((key) => parsed[key] !== undefined);
  if (headline.length) {
    const cards = document.createElement("div");
    cards.className = "detail-stat-grid";
    for (const key of headline) {
      const card = document.createElement("div");
      card.className = "detail-stat-card";
      const label = document.createElement("span");
      label.textContent = fieldLabel(key);
      const value = document.createElement("strong");
      value.textContent = key === "confidence" ? `${Math.round(Number(parsed[key]) * 100)}%` : humanValue(parsed[key]);
      card.append(label, value);
      cards.append(card);
    }
    parent.append(cards);
  }
  for (const key of ["summary", "thesis", "risk_summary", "error", "message"]) {
    if (parsed[key]) appendInfoBlock(parent, fieldLabel(key), parsed[key], "detail-info-block emphasis");
  }
  if (Array.isArray(parsed.errors) && parsed.errors.length) {
    const table = document.createElement("table");
    table.className = "structured-table";
    table.innerHTML = "<thead><tr><th>问题</th><th>位置</th><th>说明</th></tr></thead>";
    const body = document.createElement("tbody");
    for (const issue of parsed.errors) {
      const row = document.createElement("tr");
      for (const key of ["code", "path", "message"]) {
        const cell = document.createElement("td");
        cell.textContent = issue?.[key] || "—";
        row.append(cell);
      }
      body.append(row);
    }
    table.append(body);
    parent.append(table);
  }
  for (const key of ["key_evidence_ids", "checked_evidence_ids", "missing_data", "allowed_actions", "adopted_claim_ids", "rejected_claim_ids"]) {
    if (Array.isArray(parsed[key]) && parsed[key].length) {
      const title = document.createElement("div");
      title.className = "detail-list-title";
      title.textContent = fieldLabel(key);
      parent.append(title);
      appendChips(parent, parsed[key]);
    }
  }
  for (const key of ["causal_chains", "findings", "rejected_claims", "evidence_conflicts", "invalidation_conditions"]) {
    if (!Array.isArray(parsed[key]) || !parsed[key].length) continue;
    const title = document.createElement("h5");
    title.textContent = fieldLabel(key);
    parent.append(title);
    for (const item of parsed[key].slice(0, 8)) {
      const card = document.createElement("article");
      card.className = "detail-nested-card";
      const itemTitle = item.claim_id || item.dimension || item.reason_code || item.metric || `条目 ${parsed[key].indexOf(item) + 1}`;
      const strong = document.createElement("strong");
      strong.textContent = humanValue(itemTitle);
      card.append(strong);
      for (const itemKey of ["observed_fact", "inference", "transmission_mechanism", "expected_effect", "effective_window", "statement", "reason", "description", "condition", "rationale"]) {
        if (item[itemKey]) appendInfoBlock(card, fieldLabel(itemKey), item[itemKey], "nested-info");
      }
      appendChips(card, item.evidence_ids || item.shared_evidence_ids);
      parent.append(card);
    }
  }
  const skip = new Set(["valid", "mode", "errors", "warnings", ...headlineKeys, "summary", "thesis", "risk_summary", "error", "message", "key_evidence_ids", "checked_evidence_ids", "missing_data", "allowed_actions", "adopted_claim_ids", "rejected_claim_ids", "causal_chains", "findings", "rejected_claims", "evidence_conflicts", "invalidation_conditions"]);
  const remaining = Object.entries(parsed).filter(([key, value]) => !skip.has(key) && value !== undefined && value !== null && value !== "").slice(0, 14);
  if (remaining.length) {
    const table = document.createElement("table");
    table.className = "structured-table key-value-table compact-detail-table";
    table.innerHTML = "<thead><tr><th>字段</th><th>内容</th></tr></thead>";
    const body = document.createElement("tbody");
    for (const [key, value] of remaining) {
      const row = document.createElement("tr");
      const keyCell = document.createElement("th");
      keyCell.textContent = fieldLabel(key);
      const valueCell = document.createElement("td");
      valueCell.textContent = humanValue(value);
      row.append(keyCell, valueCell);
      body.append(row);
    }
    table.append(body);
    parent.append(table);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
}

function hideDetail() {
  elements.detailEmpty.classList.remove("hidden");
  elements.eventDetail.classList.add("hidden");
}

function pretty(value) {
  if (value === undefined || value === null) return "—";
  if (typeof value === "string") {
    const parsed = window.FxRunView.extractJson(value);
    return parsed ? JSON.stringify(parsed, null, 2) : value;
  }
  return JSON.stringify(value, null, 2);
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function showResult(result) {
  if (result?.data_preview && typeof result.data_preview === "object") {
    state.dataPreview = result.data_preview;
  }
  elements.resultSummary.classList.remove("hidden");
  elements.decision.textContent = result?.decision?.decision || "—";
  const confidence = Number(result?.decision?.confidence);
  elements.confidence.textContent = Number.isFinite(confidence)
    ? `${Math.round(confidence * 100)}%`
    : "—";
  elements.reportContent.textContent =
    result?.report_markdown || pretty(result || {});
  renderReportPage();
  if (result?.run_id) elements.swarmId.textContent = result.run_id;
  const canonical = result?.decision?.display_symbol || result?.decision?.canonical_symbol;
  if (canonical && elements.symbol) {
    const normalized = canonical.includes("/")
      ? canonical
      : `${canonical.slice(0, 3)}/${canonical.slice(3)}`;
    const option = [...elements.symbol.options].find((item) => item.value.replace("/", "") === normalized.replace("/", ""));
    if (option) elements.symbol.value = option.value;
    updateSymbolChrome();
  }
  if (result?.evidence_context_id) {
    elements.contextBadge.textContent = result.evidence_context_id;
    elements.contextBadge.classList.add("ready");
  }
  renderCanvas();
  renderDataHealth();
  renderDataPreview();
  renderChat();
}

function finishRun(status, error = "") {
  state.running = false;
  state.active.clear();
  renderActiveCalls();
  stopPolling();
  localStorage.removeItem("fx-debate-job-id");
  setChip(
    elements.runStatus,
    status === "completed" ? "已完成" : "失败",
    status,
  );
  elements.consoleMessage.textContent =
    status === "completed" ? "运行完成，可查看最终报告。" : error;
  elements.formMessage.textContent =
    status === "completed" ? "可以调整参数后再次运行。" : error;
  elements.formMessage.classList.toggle("error", status !== "completed");
  loadConversations();
  updateRunButton();
  updateClock();
}

function updateClock() {
  if (!state.startedAt) {
    elements.elapsed.textContent = "00:00";
    return;
  }
  const seconds = Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000));
  const minutes = Math.floor(seconds / 60);
  elements.elapsed.textContent = `${String(minutes).padStart(2, "0")}:${String(
    seconds % 60,
  ).padStart(2, "0")}`;
}

async function resumeSavedJob() {
  const saved = localStorage.getItem("fx-debate-job-id");
  if (!saved) return;
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(saved)}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      localStorage.removeItem("fx-debate-job-id");
      return;
    }
    const job = await response.json();
    prepareNewRun();
    state.jobId = saved;
    state.conversationId = job.conversation_id || state.conversationId;
    state.startedAt = Date.parse(job.started_at || job.created_at) || Date.now();
    elements.jobId.textContent = saved;
    if (job.status === "completed") {
      await pollEvents();
      const input = state.events.find((event) => event.type === "worker_started")?.data?.input || {};
      state.goal = input.goal || input.user_prompt || "";
      state.result = job.result;
      showResult(job.result);
      finishRun("completed");
    } else if (job.status === "failed") {
      await pollEvents();
      finishRun("failed", job.error || "Debate 运行失败");
    } else {
      startPolling();
    }
  } catch (_error) {
    localStorage.removeItem("fx-debate-job-id");
  }
}

elements.form.addEventListener("submit", startRun);
elements.themeToggle?.addEventListener("click", () => {
  const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(nextTheme);
});
elements.symbol?.addEventListener("change", () => {
  updateSymbolChrome();
  renderChat();
});
elements.chatForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  elements.confirmCost.checked = true;
  startRun(event);
});
elements.chatInput?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});
elements.chatSuggestions?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-prompt]");
  if (!button || !elements.chatInput) return;
  elements.chatInput.value = button.dataset.prompt || "";
  elements.chatInput.focus();
  elements.chatInput.dispatchEvent(new Event("input", { bubbles: true }));
});
elements.apiSettingsForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  saveSettings(
    {
      provider: elements.settingProvider.value.trim(),
      model: elements.settingModel.value.trim(),
      base_url: elements.settingBaseUrl.value.trim(),
      api_key: elements.settingApiKey.value || null,
      reasoning_effort: elements.settingReasoning.value,
    },
    elements.apiSettingsStatus,
  );
});
elements.dataSettingsForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  saveSettings(
    {
      data_source: elements.settingDataSource.value,
      excel_path: elements.settingExcelPath.value.trim(),
      db_host: elements.settingDbHost.value.trim(),
      db_port: Number(elements.settingDbPort.value) || null,
      db_name: elements.settingDbName.value.trim(),
      db_user: elements.settingDbUser.value.trim(),
      db_password: elements.settingDbPassword.value || null,
    },
    elements.dataSettingsStatus,
  );
});
elements.useSyntheticData?.addEventListener("click", () => {
  const path = elements.useSyntheticData.title;
  if (!path || path === "尚未生成合成数据") return;
  elements.settingDataSource.value = "excel";
  elements.settingExcelPath.value = path;
  elements.settingsNote.textContent = "已填入完整合成数据路径，点击“保存数据设置”后生效。";
});
elements.viewTabs?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (button) setView(button.dataset.view);
});
elements.newConversation?.addEventListener("click", () => {
  stopPolling();
  localStorage.removeItem("fx-debate-job-id");
  state.conversationId = null;
  state.jobId = null;
  state.running = false;
  prepareNewRun();
  state.running = false;
  setView("chat");
  setChip(elements.runStatus, "未运行", "idle");
  elements.consoleMessage.textContent = "新会话已创建，输入研究目标后开始。";
  updateRunButton();
  renderConversations();
});
elements.confirmCost.addEventListener("change", updateRunButton);
elements.filters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]");
  if (!button) return;
  state.filter = button.dataset.filter;
  for (const item of elements.filters.querySelectorAll("button")) {
    item.classList.toggle("active", item === button);
  }
  renderEvents();
});
elements.copyDetail.addEventListener("click", async () => {
  const selected = state.events.find(
    (event) => event.sequence === state.selectedSequence,
  );
  if (!selected) return;
  await navigator.clipboard.writeText(pretty(selected));
  elements.copyDetail.textContent = "已复制";
  window.setTimeout(() => {
    elements.copyDetail.textContent = "复制";
  }, 1200);
});
elements.copyDiagnostics?.addEventListener("click", async () => {
  if (!navigator.clipboard || !state.diagnostics.length) return;
  await navigator.clipboard.writeText(pretty(state.diagnostics));
  elements.copyDiagnostics.textContent = "已复制";
  window.setTimeout(() => {
    elements.copyDiagnostics.textContent = "复制诊断";
  }, 1200);
});
elements.copyContext?.addEventListener("click", async () => {
  if (!navigator.clipboard) return;
  await navigator.clipboard.writeText(elements.contextJson.textContent || "—");
  elements.copyContext.textContent = "已复制";
  window.setTimeout(() => {
    elements.copyContext.textContent = "复制 Context";
  }, 1200);
});
elements.dataPreviewTabs?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-preview-domain]");
  if (!button) return;
  state.dataPreviewDomain = button.dataset.previewDomain || "all";
  renderDataPreview();
});
elements.dataPreviewDomain?.addEventListener("change", (event) => {
  state.dataPreviewDomain = event.target.value || "all";
  renderDataPreview();
});
elements.copyReport?.addEventListener("click", async () => {
  if (!navigator.clipboard) return;
  await navigator.clipboard.writeText(elements.reportViewContent.textContent || "—");
  elements.copyReport.textContent = "已复制";
  window.setTimeout(() => {
    elements.copyReport.textContent = "复制可读报告";
  }, 1200);
});
elements.showReport.addEventListener("click", () => elements.reportDialog.showModal());
elements.closeReport.addEventListener("click", () => elements.reportDialog.close());

function setupResizablePanes() {
  const workspace = document.querySelector(".workspace");
  if (!workspace) return;
  document.body.classList.remove("resizing-panes");
  document.body.classList.remove("resizing-panes-center");
  const consoleBody = document.querySelector(".console-body");
  const defaults = { left: 260, right: 270, center: "46%" };
  const limits = { left: [210, 420], right: [210, 380] };
  const setOuterWidth = (side, value) => {
    workspace.style.setProperty(`--${side}-width`, value);
    document.documentElement.style.setProperty(`--${side}-width`, value);
  };
  let activeStop = null;
  const stopActiveResize = () => {
    activeStop?.();
    activeStop = null;
  };
  window.addEventListener("pointerup", stopActiveResize, true);
  window.addEventListener("pointercancel", stopActiveResize, true);
  window.addEventListener("blur", stopActiveResize);
  for (const splitter of workspace.querySelectorAll(".pane-splitter")) {
    const side = splitter.dataset.resize;
    const isCenter = side === "center";
    const target = isCenter ? consoleBody : workspace;
    if (!target) continue;
    const resize = (event) => {
      const rect = target.getBoundingClientRect();
      if (isCenter) {
        const min = 180;
        const max = Math.max(min, rect.height - 220);
        const raw = event.clientY - rect.top;
        target.style.setProperty("--event-height", `${Math.max(min, Math.min(max, raw))}px`);
        return;
      }
      const raw = side === "left" ? event.clientX - rect.left : rect.right - event.clientX;
      const [min, max] = limits[side];
      setOuterWidth(side, `${Math.max(min, Math.min(max, raw))}px`);
    };
    splitter.addEventListener("pointerdown", (event) => {
      if (window.matchMedia("(max-width: 780px)").matches) return;
      event.preventDefault();
      splitter.setPointerCapture?.(event.pointerId);
      splitter.classList.add("dragging");
      document.body.classList.add("resizing-panes");
      if (isCenter) document.body.classList.add("resizing-panes-center");
      const move = (moveEvent) => resize(moveEvent);
      const stop = () => {
        splitter.classList.remove("dragging");
        document.body.classList.remove("resizing-panes");
        document.body.classList.remove("resizing-panes-center");
        splitter.removeEventListener("pointermove", move);
        splitter.removeEventListener("pointerup", stop);
        splitter.removeEventListener("pointercancel", stop);
        if (activeStop === stop) activeStop = null;
      };
      activeStop = stop;
      splitter.addEventListener("pointermove", move);
      splitter.addEventListener("pointerup", stop, { once: true });
      splitter.addEventListener("pointercancel", stop, { once: true });
    });
    splitter.addEventListener("dblclick", () => {
      if (isCenter) target.style.setProperty("--event-height", defaults.center);
      else setOuterWidth(side, `${defaults[side]}px`);
    });
    splitter.addEventListener("keydown", (event) => {
      const acceptedKeys = isCenter ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
      if (!acceptedKeys.includes(event.key)) return;
      event.preventDefault();
      if (isCenter) {
        const current = parseFloat(getComputedStyle(target).gridTemplateRows) || 320;
        const delta = event.key === "ArrowDown" ? 16 : -16;
        const max = Math.max(180, target.getBoundingClientRect().height - 220);
        target.style.setProperty("--event-height", `${Math.max(180, Math.min(max, current + delta))}px`);
        return;
      }
      const current = parseFloat(getComputedStyle(target).getPropertyValue(`--${side}-width`)) || defaults[side];
      const delta = (side === "left" ? event.key === "ArrowRight" : event.key === "ArrowLeft") ? 16 : -16;
      const [min, max] = limits[side];
      setOuterWidth(side, `${Math.max(min, Math.min(max, current + delta))}px`);
    });
  }
}

resetAgents();
initTheme();
updateSymbolChrome();
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
window.scrollTo(0, 0);
setupResizablePanes();
setView("chat");
renderChat();
loadConversations();
checkHealth().then(resumeSavedJob);
