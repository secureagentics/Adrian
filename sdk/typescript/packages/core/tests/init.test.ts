import { afterEach, describe, expect, it, vi } from "vitest";
import { currentConfig, resolveInitOptions } from "../src/config.js";
import { getWebSocketClient, init, shutdown } from "../src/index.js";

describe("resolveInitOptions", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("honours explicit wsUrl: null over ADRIAN_WS_URL", () => {
    vi.stubEnv("ADRIAN_WS_URL", "wss://env.example/ws");
    expect(resolveInitOptions({ wsUrl: null }).wsUrl).toBeNull();
  });

  it("honours explicit wsUrl over ADRIAN_WS_URL", () => {
    vi.stubEnv("ADRIAN_WS_URL", "wss://env.example/ws");
    expect(resolveInitOptions({ wsUrl: "wss://explicit.example/ws" }).wsUrl).toBe("wss://explicit.example/ws");
  });

  it("honours explicit blockTimeout over ADRIAN_BLOCK_TIMEOUT", () => {
    vi.stubEnv("ADRIAN_BLOCK_TIMEOUT", "99");
    expect(resolveInitOptions({ blockTimeout: 10 }).blockTimeout).toBe(10);
  });
});

describe("init option resolution", () => {
  afterEach(async () => {
    vi.unstubAllEnvs();
    await shutdown();
  });

  it("does not create a WebSocket client when wsUrl is explicitly null", async () => {
    vi.stubEnv("ADRIAN_WS_URL", "wss://env.example/ws");
    await init({ wsUrl: null, handlers: [] });
    expect(getWebSocketClient()).toBeNull();
    expect(currentConfig()?.wsUrl).toBeNull();
  });

  it("stores explicit blockTimeout when env is set", async () => {
    vi.stubEnv("ADRIAN_BLOCK_TIMEOUT", "99");
    await init({ blockTimeout: 10, handlers: [] });
    expect(currentConfig()?.blockTimeout).toBe(10);
  });
});
