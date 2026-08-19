import { describe, expect, it } from "vitest";
import { clearApiConfig, defaultApiConfig, readApiConfig, saveApiConfig } from "@/lib/api_config";

describe("browser API configuration", () => {
  it("round-trips saved connection settings without changing defaults", () => {
    clearApiConfig();
    const defaults = defaultApiConfig();
    saveApiConfig({ ...defaults, backendUrl: "http://localhost:8899", authToken: "local-token", model: "deepseek-chat" });
    expect(readApiConfig()).toMatchObject({ backendUrl: "http://localhost:8899", authToken: "local-token", model: "deepseek-chat" });
    clearApiConfig();
    expect(readApiConfig()).toMatchObject({ backendUrl: defaults.backendUrl, authToken: "", model: "" });
  });
});
