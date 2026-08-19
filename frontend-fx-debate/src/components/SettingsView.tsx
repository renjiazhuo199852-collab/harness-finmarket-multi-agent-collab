import { useEffect, useMemo, useState } from "react";
import type { ReactElement } from "react";
import { AlertCircle, CheckCircle2, Eye, EyeOff, KeyRound, Play, RotateCcw, Save, Server, ShieldCheck } from "lucide-react";
import { api, type ConnectionProbe, type DataSourceSettings, type LlmProviderOption, type LlmSettings } from "@/lib/api";
import { clearApiConfig, defaultApiConfig, normalizeBaseUrl, readApiConfig, saveApiConfig, type ApiConfig } from "@/lib/api_config";

type ProbeState = { kind: "success" | "error"; message: string; detail?: string };

const FALLBACK_PROVIDERS: LlmProviderOption[] = [
  { name: "openai", label: "OpenAI", base_url_env: "OPENAI_BASE_URL", default_model: "gpt-5.5", default_base_url: "https://api.openai.com/v1", api_key_required: true },
  { name: "custom", label: "自定义 OpenAI-compatible API", base_url_env: "CUSTOM_BASE_URL", default_model: "", default_base_url: "", api_key_required: false },
  { name: "deepseek", label: "DeepSeek", base_url_env: "DEEPSEEK_BASE_URL", default_model: "deepseek-chat", default_base_url: "https://api.deepseek.com/v1", api_key_required: true },
  { name: "siliconflow-cn", label: "SiliconFlow (CN)", base_url_env: "SILICONFLOW_BASE_URL", default_model: "deepseek-ai/DeepSeek-V3.1-Terminus", default_base_url: "https://api.siliconflow.cn/v1", api_key_required: true },
  { name: "moonshot", label: "Moonshot / Kimi", base_url_env: "MOONSHOT_BASE_URL", default_model: "kimi-k2.6", default_base_url: "https://api.moonshot.ai/v1", api_key_required: true },
  { name: "ollama", label: "Ollama", base_url_env: "OLLAMA_BASE_URL", default_model: "qwen2.5:32b", default_base_url: "http://localhost:11434", api_key_required: false },
];

function supportedReasoningEffort(value?: string): string {
  return value && ["low", "medium", "high", "max"].includes(value) ? value : "";
}

function SecretInput({ value, onChange, placeholder, id }: { value: string; onChange: (value: string) => void; placeholder: string; id: string }): ReactElement {
  const [visible, setVisible] = useState(false);
  return <div className="secret-input"><input id={id} type={visible ? "text" : "password"} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} autoComplete="off" /><button type="button" className="input-icon-button" title={visible ? "隐藏密钥" : "显示密钥"} onClick={() => setVisible((current) => !current)}>{visible ? <EyeOff size={15} /> : <Eye size={15} />}</button></div>;
}

function ProbeNotice({ state }: { state: ProbeState | null }): ReactElement | null {
  if (!state) return null;
  return <div className={`probe-notice probe-${state.kind}`}><span>{state.kind === "success" ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}</span><div><strong>{state.message}</strong>{state.detail && <small>{state.detail}</small>}</div></div>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactElement }): ReactElement {
  return <label className="settings-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function probeDetail(probe: ConnectionProbe): string {
  const readiness = probe.readiness.ok ? "运行就绪" : `运行未就绪：${probe.readiness.message}`;
  return `健康检查 ${probe.health.status} · ${readiness}`;
}

function serverSettingsDetail(llm: LlmSettings | null, dataSources: DataSourceSettings | null): string {
  const llmText = llm ? `模型运行时 ${llm.provider}/${llm.model_name}` : "模型运行时未读取";
  const dataText = dataSources ? `数据凭据 ${dataSources.tushare_token_configured ? "已配置" : "未配置"}` : "数据凭据未读取";
  return `${llmText} · ${dataText}`;
}

export function SettingsView(): ReactElement {
  const [config, setConfig] = useState<ApiConfig>(() => readApiConfig());
  const [llm, setLlm] = useState<LlmSettings | null>(null);
  const [dataSources, setDataSources] = useState<DataSourceSettings | null>(null);
  const [tushareToken, setTushareToken] = useState("");
  const [clearProviderKey, setClearProviderKey] = useState(false);
  const [clearTushareToken, setClearTushareToken] = useState(false);
  const [loadNotice, setLoadNotice] = useState<ProbeState | null>(null);
  const [saveNotice, setSaveNotice] = useState<ProbeState | null>(null);
  const [saved, setSaved] = useState(false);
  const [backendProbe, setBackendProbe] = useState<ProbeState | null>(null);
  const [providerProbe, setProviderProbe] = useState<ProbeState | null>(null);
  const [testingBackend, setTestingBackend] = useState(false);
  const [testingProvider, setTestingProvider] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    setLoadNotice(null);
    void api.getLlmSettings().then((settings) => {
      if (!active) return;
      setLlm(settings);
      setConfig((current) => ({ ...current, provider: settings.provider, model: settings.model_name, providerBaseUrl: settings.base_url }));
    }).catch((error) => {
      if (active) setLoadNotice({ kind: "error", message: "未读取到服务端模型配置", detail: error instanceof Error ? error.message : "请先启动后端或检查 API 地址" });
    });
    void api.getDataSourceSettings().then((settings) => {
      if (active) setDataSources(settings);
    }).catch((error) => {
      if (active) setLoadNotice({ kind: "error", message: "未读取到服务端数据源配置", detail: error instanceof Error ? error.message : "请先启动后端或检查 API 地址" });
    });
    return () => { active = false; };
  }, []);

  const update = <K extends keyof ApiConfig>(key: K, value: ApiConfig[K]) => {
    setConfig((current) => ({ ...current, [key]: value }));
    setSaved(false);
    setSaveNotice(null);
  };

  const providers = useMemo(() => {
    const source = llm?.providers?.length ? llm.providers : FALLBACK_PROVIDERS;
    const withCustom = source.some((provider) => provider.name === "custom")
      ? source
      : [...source, FALLBACK_PROVIDERS.find((provider) => provider.name === "custom")!];
    return withCustom.some((provider) => provider.name === config.provider)
      ? withCustom
      : [...withCustom, { ...FALLBACK_PROVIDERS[0], name: config.provider, label: config.provider }];
  }, [config.provider, llm]);

  const selectProvider = (name: string) => {
    const selected = providers.find((provider) => provider.name === name);
    setConfig((current) => ({
      ...current,
      provider: name,
      model: selected?.default_model || current.model,
      providerBaseUrl: selected?.default_base_url || (name === "custom" ? "" : current.providerBaseUrl),
    }));
    setClearProviderKey(false);
    setSaved(false);
    setSaveNotice(null);
  };

  const save = async () => {
    setSaving(true);
    setSaved(false);
    setSaveNotice(null);
    const localConfig = { ...config, backendUrl: normalizeBaseUrl(config.backendUrl), providerBaseUrl: normalizeBaseUrl(config.providerBaseUrl), marketDataBaseUrl: normalizeBaseUrl(config.marketDataBaseUrl) };
    saveApiConfig(localConfig);
    const failures: string[] = [];
    let effectiveLlm = llm;
    let effectiveDataSources = dataSources;
    try {
      const updated = await api.updateLlmSettings({
        provider: localConfig.provider,
        model_name: localConfig.model.trim() || providers.find((provider) => provider.name === localConfig.provider)?.default_model || "",
        base_url: localConfig.providerBaseUrl,
        api_key: localConfig.providerApiKey.trim() || undefined,
        clear_api_key: clearProviderKey,
        temperature: llm?.temperature ?? 0,
        timeout_seconds: llm?.timeout_seconds ?? 120,
        max_retries: llm?.max_retries ?? 2,
        reasoning_effort: supportedReasoningEffort(llm?.reasoning_effort),
      });
      setLlm(updated);
      effectiveLlm = updated;
      setConfig((current) => ({ ...current, provider: updated.provider, model: updated.model_name, providerBaseUrl: updated.base_url }));
      setClearProviderKey(false);
    } catch (error) {
      failures.push(`模型配置：${error instanceof Error ? error.message : "服务端未同步"}`);
    }
    if (clearTushareToken || tushareToken.trim()) {
      try {
        const updated = await api.updateDataSourceSettings({ tushare_token: tushareToken.trim() || undefined, clear_tushare_token: clearTushareToken });
        setDataSources(updated);
        effectiveDataSources = updated;
        setClearTushareToken(false);
        setTushareToken("");
      } catch (error) {
        failures.push(`数据源：${error instanceof Error ? error.message : "服务端未同步"}`);
      }
    }
    setSaved(true);
    setSaveNotice(failures.length ? { kind: "error", message: "本地配置已保存，但服务端未完全同步", detail: failures.join("；") } : { kind: "success", message: "配置已保存并同步到服务端", detail: serverSettingsDetail(effectiveLlm, effectiveDataSources) });
    setSaving(false);
  };

  const reset = () => {
    clearApiConfig();
    setConfig(defaultApiConfig());
    setSaved(false);
    setSaveNotice(null);
    setBackendProbe(null);
    setProviderProbe(null);
    setClearProviderKey(false);
    setClearTushareToken(false);
    setTushareToken("");
  };

  const testBackend = async () => {
    saveApiConfig({ ...config, backendUrl: normalizeBaseUrl(config.backendUrl) });
    setTestingBackend(true);
    setBackendProbe(null);
    try {
      const probe = await api.testConnection();
      setBackendProbe({ kind: probe.readiness.ok ? "success" : "error", message: probe.readiness.ok ? "后端连接正常" : "后端可访问，但尚未就绪", detail: probeDetail(probe) });
    } catch (error) {
      setBackendProbe({ kind: "error", message: "后端连接失败", detail: error instanceof Error ? error.message : "无法访问配置的 API 地址" });
    } finally {
      setTestingBackend(false);
    }
  };

  const testProvider = async () => {
    saveApiConfig(config);
    setTestingProvider(true);
    setProviderProbe(null);
    try {
      const probe = await api.testProvider({ ...config, providerBaseUrl: normalizeBaseUrl(config.providerBaseUrl) });
      setProviderProbe({ kind: probe.ok ? "success" : "error", message: probe.ok ? "模型接口连接正常" : (probe.message || "模型接口返回错误"), detail: `${probe.endpoint}${probe.status ? ` · HTTP ${probe.status}` : ""}` });
    } catch (error) {
      setProviderProbe({ kind: "error", message: "模型接口测试失败", detail: error instanceof Error ? error.message : "无法访问模型接口，可能是地址、密钥或跨域配置问题" });
    } finally {
      setTestingProvider(false);
    }
  };

  const selectedProvider = providers.find((provider) => provider.name === config.provider);

  return <div className="workspace-view settings-view">
    <div className="view-heading"><div><span className="eyebrow">CONNECTION SETTINGS</span><h2>API 配置与测试</h2><p>统一管理工作区后端、模型供应商和行情数据服务的连接信息。</p></div><div className="settings-heading-actions"><button className="secondary-button" onClick={reset}><RotateCcw size={14} />恢复默认</button><button className="primary-button" disabled={saving} onClick={() => void save()}><Save size={14} />{saving ? "同步中…" : saved ? "已保存" : "保存配置"}</button></div></div>
    <ProbeNotice state={loadNotice} />
    <div className="settings-warning"><ShieldCheck size={16} /><span>普通连接参数保存在当前浏览器；页面输入的密钥只保存在当前标签页会话，并在点击保存后写入服务端配置。页面不会回显已保存的完整密钥。</span></div>
    <section className="settings-grid">
      <article className="settings-card"><div className="settings-card-head"><div className="settings-card-icon"><Server size={17} /></div><div><h3>工作区后端</h3><p>Session、SSE、Swarm 和健康检查使用的服务地址。</p></div></div>
        <Field label="API 服务地址" hint="留空时使用 Vite 代理或 VITE_API_URL。"><input value={config.backendUrl} onChange={(event) => update("backendUrl", event.target.value)} placeholder="http://127.0.0.1:8899" /></Field>
        <Field label="API 认证密钥" hint="服务端启用 API_AUTH_KEY 时填写 Bearer 密钥。"><SecretInput id="backend-token" value={config.authToken} onChange={(value) => update("authToken", value)} placeholder="可选" /></Field>
        <div className="settings-card-actions"><button className="secondary-button" disabled={testingBackend} onClick={() => void testBackend()}><Play size={14} />{testingBackend ? "测试中…" : "测试后端连接"}</button></div><ProbeNotice state={backendProbe} />
      </article>
      <article className="settings-card"><div className="settings-card-head"><div className="settings-card-icon"><KeyRound size={17} /></div><div><h3>模型供应商</h3><p>运行时使用 OpenAI-compatible 协议；测试由后端代请求，不受浏览器 CORS 影响。</p></div></div>
        <Field label="供应商类型"><select value={config.provider} onChange={(event) => selectProvider(event.target.value)}>{providers.map((provider) => <option key={provider.name} value={provider.name}>{provider.label}</option>)}</select></Field>
        <Field label="模型名称"><input value={config.model} onChange={(event) => update("model", event.target.value)} placeholder={selectedProvider?.default_model || "例如 deepseek-chat"} /></Field>
        <Field label="接口基地址" hint="填写 API 基地址，例如 https://your-host.example/v1；不要填网页首页或完整的 /chat/completions。后端会检查 /v1/models 或 /models。"><input value={config.providerBaseUrl} onChange={(event) => update("providerBaseUrl", event.target.value)} placeholder={selectedProvider?.default_base_url || "https://api.openai.com/v1"} /></Field>
        <Field label="模型 API 密钥" hint={llm?.api_key_configured ? "服务端已有密钥；留空表示保持不变。" : config.provider === "custom" ? "按你的自建服务要求填写；不需要鉴权时可以留空。" : selectedProvider?.api_key_required ? "当前供应商通常需要密钥。" : "当前供应商不要求密钥。"}><SecretInput id="provider-key" value={config.providerApiKey} onChange={(value) => update("providerApiKey", value)} placeholder={llm?.api_key_configured ? "已配置，留空不修改" : "可选"} /></Field>
        <label className="settings-checkbox"><input type="checkbox" checked={clearProviderKey} onChange={(event) => setClearProviderKey(event.target.checked)} />清除服务端已保存的模型密钥</label>
        <div className="settings-card-actions"><button className="secondary-button" disabled={testingProvider || !config.providerBaseUrl.trim()} onClick={() => void testProvider()}><Play size={14} />{testingProvider ? "测试中…" : "测试模型接口"}</button></div><ProbeNotice state={providerProbe} />
      </article>
      <article className="settings-card"><div className="settings-card-head"><div className="settings-card-icon"><Server size={17} /></div><div><h3>行情与证据服务</h3><p>兼容当前 Excel / PostgreSQL，并为后续 MarketData SDK 和模糊查询服务器预留入口。</p></div></div>
        <Field label="数据服务地址" hint="当前地址作为前端连接配置保留，具体查询仍由后端 SDK 决定。"><input value={config.marketDataBaseUrl} onChange={(event) => update("marketDataBaseUrl", event.target.value)} placeholder="例如 http://127.0.0.1:9000" /></Field>
        <Field label="数据服务密钥"><SecretInput id="market-key" value={config.marketDataApiKey} onChange={(value) => update("marketDataApiKey", value)} placeholder="可选" /></Field>
        <Field label="Tushare Token" hint={dataSources?.tushare_token_configured ? "服务端已有 Token；留空表示保持不变。" : "可选，保存后写入服务端。"}><SecretInput id="tushare-token" value={tushareToken} onChange={setTushareToken} placeholder={dataSources?.tushare_token_configured ? "已配置，留空不修改" : "可选"} /></Field>
        <label className="settings-checkbox"><input type="checkbox" checked={clearTushareToken} onChange={(event) => setClearTushareToken(event.target.checked)} />清除服务端已保存的 Tushare Token</label>
        <div className="settings-info"><ShieldCheck size={14} /><span>{dataSources ? `${dataSources.tushare_token_configured ? "Tushare 已配置" : "Tushare 未配置"} · ${dataSources.baostock_message}` : "正在读取服务端数据源状态…"}</span></div>
      </article>
    </section>
    <ProbeNotice state={saveNotice} />
    <section className="settings-card settings-guide"><h3>使用说明</h3><div className="settings-guide-grid"><div><strong>1</strong><span>填写后端地址并测试连接，确认健康检查和运行就绪状态。</span></div><div><strong>2</strong><span>自建服务请选择“自定义 OpenAI-compatible API”，填模型 API 基地址和模型名；服务需要提供 Bearer 鉴权及 /v1/chat/completions。</span></div><div><strong>3</strong><span>保存后配置同步到服务端，后续 Debate 运行由服务端调用模型；数据源仍以 MarketData SDK 和后端数据源为准。</span></div></div></section>
  </div>;
}
