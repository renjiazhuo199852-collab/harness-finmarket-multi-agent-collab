export interface ApiConfig {
  backendUrl: string;
  authToken: string;
  provider: string;
  model: string;
  providerBaseUrl: string;
  providerApiKey: string;
  marketDataBaseUrl: string;
  marketDataApiKey: string;
}

export const API_CONFIG_STORAGE_KEY = "fx-debate-api-config-v1";
const API_CONFIG_SECRET_STORAGE_KEY = "fx-debate-api-secrets-v1";

type PersistedApiConfig = Omit<
  ApiConfig,
  "authToken" | "providerApiKey" | "marketDataApiKey"
>;
type ApiSecrets = Pick<
  ApiConfig,
  "authToken" | "providerApiKey" | "marketDataApiKey"
>;

export function defaultApiConfig(): ApiConfig {
  return {
    backendUrl: (import.meta.env.VITE_API_URL || "").replace(/\/$/, ""),
    authToken: "",
    provider: "openai",
    model: "",
    providerBaseUrl: "https://api.openai.com/v1",
    providerApiKey: "",
    marketDataBaseUrl: "",
    marketDataApiKey: "",
  };
}

export function readApiConfig(): ApiConfig {
  const fallback = defaultApiConfig();
  try {
    const raw = localStorage.getItem(API_CONFIG_STORAGE_KEY);
    const secretRaw = sessionStorage.getItem(API_CONFIG_SECRET_STORAGE_KEY);
    const stored = raw ? (JSON.parse(raw) as Partial<PersistedApiConfig>) : {};
    const secrets = secretRaw ? (JSON.parse(secretRaw) as Partial<ApiSecrets>) : {};
    const providerAliases: Record<string, string> = {
      "OpenAI-compatible": "custom",
      DeepSeek: "deepseek",
      SiliconFlow: "siliconflow-cn",
      Moonshot: "moonshot",
      Custom: "custom",
    };
    const provider = typeof stored.provider === "string"
      ? providerAliases[stored.provider] || stored.provider
      : fallback.provider;
    return { ...fallback, ...stored, ...secrets, provider };
  } catch {
    return fallback;
  }
}

export function saveApiConfig(config: ApiConfig): void {
  const { authToken, providerApiKey, marketDataApiKey, ...persisted } = config;
  localStorage.setItem(
    API_CONFIG_STORAGE_KEY,
    JSON.stringify(persisted satisfies PersistedApiConfig),
  );
  sessionStorage.setItem(
    API_CONFIG_SECRET_STORAGE_KEY,
    JSON.stringify({ authToken, providerApiKey, marketDataApiKey } satisfies ApiSecrets),
  );
}

export function clearApiConfig(): void {
  localStorage.removeItem(API_CONFIG_STORAGE_KEY);
  sessionStorage.removeItem(API_CONFIG_SECRET_STORAGE_KEY);
}

export function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/$/, "");
}
