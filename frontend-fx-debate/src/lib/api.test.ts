import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { clearApiConfig } from "@/lib/api_config";

describe("session API", () => {
  beforeEach(() => {
    clearApiConfig();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearApiConfig();
  });

  it("deletes a session with an encoded path and DELETE method", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify({ status: "deleted", session_id: "session/with space" })),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.deleteSession("session/with space")).resolves.toEqual({
      status: "deleted",
      session_id: "session/with space",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/sessions/session%2Fwith%20space",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
