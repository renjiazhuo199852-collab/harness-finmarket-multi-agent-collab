import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { clearApiConfig } from "@/lib/api_config";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(JSON.stringify(body)),
  } as Response;
}

function installFetchMock(handler: (path: string) => Response | Promise<Response>): void {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => handler(String(input))));
}

describe("backend liveness status", () => {
  beforeEach(() => {
    clearApiConfig();
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    clearApiConfig();
  });

  it("shows a checking state while /live is pending", () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return new Promise<Response>(() => undefined);
      if (path === "/live") return new Promise<Response>(() => undefined);
      return jsonResponse({}, 404);
    });

    render(<App />);

    expect(screen.getByText("正在检查后端服务")).toBeTruthy();
  });

  it("shows online when /live returns HTTP 200", async () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/live") return jsonResponse({ status: "healthy", service: "Vibe-Trading API" });
      return jsonResponse({}, 404);
    });

    render(<App />);

    expect(await screen.findByText("后端服务在线")).toBeTruthy();
    expect(screen.getByTitle("Vibe-Trading 后端 API 可访问")).toBeTruthy();
  });

  it("shows offline when /live cannot be reached", async () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/live") return Promise.reject(new TypeError("connection refused"));
      return jsonResponse({}, 404);
    });

    render(<App />);

    expect(await screen.findByText("后端服务未连接")).toBeTruthy();
  });

  it("shows an error state when /live returns a non-2xx response", async () => {
    installFetchMock((path) => {
      if (path === "/sessions?limit=50") return jsonResponse([]);
      if (path === "/live") return jsonResponse({ detail: "maintenance" }, 503);
      return jsonResponse({}, 404);
    });

    render(<App />);

    expect(await screen.findByText("后端服务异常")).toBeTruthy();
  });
});
