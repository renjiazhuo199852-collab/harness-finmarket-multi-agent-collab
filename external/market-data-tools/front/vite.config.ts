import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 开发服务器把 /tools 请求转给可移植后端，浏览器因此只需要记住一个前端地址。
// 代理目标支持通过 AI_SEARCH_TOOLS_API_TARGET 临时切换，便于连接本地或云端后端，
// 默认使用可移植后端的 8011 端口，与 README 中的启动命令保持一致。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiTarget = env.AI_SEARCH_TOOLS_API_TARGET || "http://127.0.0.1:8011";

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/tools": {
          // 8000 已分配给 Docker 中的 Attu，AI Search 后端使用 8011 避免冲突。
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
