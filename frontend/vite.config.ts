import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 后端地址默认取本机 8000 端口。
// 阶段 0 只做连通性验证，前端直接跨域调用后端（后端已放行 5173 来源）。
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
