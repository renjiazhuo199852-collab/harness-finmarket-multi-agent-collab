import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const API_PATHS = ["/sessions", "/swarm/presets", "/swarm/runs", "/auth", "/health", "/ready", "/live", "/settings"];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_URL || "http://127.0.0.1:8899";
  return {
    plugins: [react()],
    resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
    server: {
      port: 5898,
      proxy: Object.fromEntries(API_PATHS.map((route) => [route, { target, changeOrigin: true }])),
    },
  };
});
