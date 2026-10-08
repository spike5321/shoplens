import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 默认同源 /api 代理，避免 localhost/127.0.0.1 混用导致 Cookie 丢失。
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target:
          loadEnv(mode, "..", "SHOPLENS_").SHOPLENS_API_PROXY ??
          "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
}));
