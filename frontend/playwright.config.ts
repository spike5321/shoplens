import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 45000,
  expect: { timeout: 10000 },
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    channel:
      process.env.SHOPLENS_BROWSER_CHANNEL ??
      (process.platform === "win32" ? "msedge" : "chromium"),
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: false,
    env: {
      SHOPLENS_API_PROXY: "http://127.0.0.1:18004",
      VITE_API_BASE_URL: "",
    },
    timeout: 30000,
  },
});
