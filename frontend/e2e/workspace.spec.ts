import { test, expect } from "@playwright/test";
import type { BrowserContext, Page } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { resolve } from "node:path";
import { once } from "node:events";
import { mkdir } from "node:fs/promises";

const root = resolve("..");
const python =
  process.env.SHOPLENS_TEST_PYTHON ??
  resolve(root, ".venv", "Scripts", "python.exe");
let server: ChildProcess;
const tokens: string[] = [];
const contexts: BrowserContext[] = [];

async function startServer() {
  server = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "app.main:app",
      "--app-dir",
      "backend",
      "--host",
      "127.0.0.1",
      "--port",
      "18004",
      "--no-access-log",
    ],
    {
      cwd: root,
      windowsHide: true,
      stdio: "ignore",
      env: {
        ...process.env,
        SHOPLENS_MODEL_PROVIDER: "offline",
        SHOPLENS_SQL_STATEMENT_TIMEOUT_MS: "15000",
        SHOPLENS_ENV: "local",
        PYTHONUTF8: "1",
      },
    },
  );
  await expect
    .poll(
      async () => {
        if (server.exitCode !== null)
          throw new Error(`Test API exited ${server.exitCode}`);
        try {
          return (await fetch("http://127.0.0.1:18004/healthz")).status;
        } catch {
          return 0;
        }
      },
      { timeout: 15000 },
    )
    .toBe(200);
}
async function stopServer() {
  if (!server || server.exitCode !== null) return;
  if (process.platform === "win32")
    execFileSync("taskkill", ["/PID", String(server.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
  else server.kill("SIGKILL");
  if (server.exitCode === null) await once(server, "exit");
}
async function remember(context: BrowserContext) {
  const cookie = (await context.cookies()).find(
    (c) => c.name === "shoplens_session",
  );
  if (cookie) tokens.push(cookie.value);
}
async function ask(page: Page, question: string, status = "已完成") {
  await page.getByLabel("你想了解什么？").fill(question);
  await page.getByRole("button", { name: "开始查询" }).click();
  await expect(page.getByRole("status")).toHaveText(status);
  return new URL(page.url()).searchParams.get("run")!;
}
async function csrf(context: BrowserContext) {
  return (await (await context.request.get("/api/session")).json()).csrf_token;
}
test.beforeAll(startServer);
test.afterAll(async () => {
  for (const context of contexts) {
    await remember(context);
    await context.close();
  }
  await stopServer();
  const cleanup = spawn(python, ["scripts/browser_fixture.py", "cleanup"], {
    cwd: root,
    windowsHide: true,
    stdio: ["pipe", "ignore", "ignore"],
  });
  cleanup.stdin!.end(tokens.join("\n"));
  const [code] = await once(cleanup, "exit");
  expect(code).toBe(0);
});

test("真实查询、图表、澄清、失败、刷新、隔离和进程重启", async ({
  browser,
}) => {
  const first = await browser.newContext({ baseURL: "http://127.0.0.1:5173" });
  contexts.push(first);
  const second = await browser.newContext({ baseURL: "http://127.0.0.1:5173" });
  contexts.push(second);
  const page = await first.newPage();
  const other = await second.newPage();
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("/");
  await expect(page.getByText("离线演示模型", { exact: true })).toBeVisible();
  const normal = await ask(page, "9月各渠道实付金额");
  await expect(page.getByRole("img", { name: /条形图/ })).toBeVisible();
  await expect(page.locator(".result table tbody tr")).toHaveCount(3);
  const rows = await page.locator(".result table tbody").innerText();
  await page.reload();
  await expect(page.getByRole("status")).toHaveText("已完成");
  await expect(page.locator(".chart canvas")).toBeVisible();
  expect(await page.locator(".result table tbody").innerText()).toBe(rows);
  await page.locator(".trace summary").click();
  await expect(page.locator("pre")).toContainText("analytics.sales_lines");
  await mkdir(resolve(root, ".local", "screenshots"), { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: resolve(root, ".local", "screenshots", "stage4-workspace.png"),
    fullPage: true,
  });
  await other.goto("/");
  await expect(other.getByRole("button", { name: "开始查询" })).toBeDisabled();
  const tokenB = await csrf(second);
  expect((await second.request.get(`/api/runs/${normal}`)).status()).toBe(404);
  expect(
    (await (await second.request.get("/api/runs")).json()).runs,
  ).toHaveLength(0);
  expect(
    (
      await second.request.post(`/api/runs/${normal}/clarify`, {
        data: { text: "实付", revision: 0 },
        headers: { "x-csrf-token": tokenB },
      })
    ).status(),
  ).toBe(404);
  expect(
    (
      await second.request.post(`/api/runs/${normal}/retry`, {
        data: {},
        headers: { "x-csrf-token": tokenB },
      })
    ).status(),
  ).toBe(404);
  await ask(page, "9月每天的订单数");
  await expect(page.getByRole("img", { name: /折线图/ })).toBeVisible();
  await ask(page, "9月退款金额与8月比较");
  await expect(page.getByRole("heading", { name: "与基期比较" })).toBeVisible();
  const paused = await ask(page, "9月销售额是多少？", "待补充");
  const saved = await (await first.request.get(`/api/runs/${paused}`)).json();
  // 真正终止后端进程，再启动：完成结果仍在，等待澄清的 checkpoint 可恢复。
  await stopServer();
  await startServer();
  await page.reload();
  await expect(page.getByRole("status")).toHaveText("待补充");
  await page.getByLabel("补充说明").fill("实付金额");
  await page.getByRole("button", { name: "提交补充" }).click();
  await expect(page.getByRole("status")).toHaveText("已完成");
  const tokenA = await csrf(first);
  expect(
    (
      await first.request.post(`/api/runs/${paused}/clarify`, {
        data: { text: "实付金额", revision: saved.revision },
        headers: { "x-csrf-token": tokenA },
      })
    ).status(),
  ).toBe(409);
  expect(
    (await (await first.request.get(`/api/runs/${normal}`)).json()).status,
  ).toBe("succeeded");
  await ask(page, "2026-09-10 到 2026-09-01 实付金额", "查询失败");
  await expect(page.getByRole("button", { name: "重新查询" })).toBeVisible();
  await ask(page, "预测10月利润", "暂不支持");
  await expect(
    page.getByText("首版只支持五个经营指标的描述性查询"),
  ).toBeVisible();
  // 锁真实业务表，让 reader 查询停在数据库中；随后杀死进程检验运行中断。
  const blocker = spawn(python, ["scripts/browser_fixture.py", "block"], {
    cwd: root,
    windowsHide: true,
    stdio: ["pipe", "pipe", "ignore"],
  });
  try {
    await new Promise<void>((resolve, reject) => {
      blocker.stdout!.once("data", (data) =>
        String(data).includes("LOCKED")
          ? resolve()
          : reject(new Error("DB lock failed")),
      );
      blocker.once("exit", () => reject(new Error("DB lock helper exited")));
    });
    await page.getByLabel("你想了解什么？").fill("9月实付金额");
    await page.getByRole("button", { name: "开始查询" }).click();
    await expect(page.getByRole("status")).toHaveText("查询中");
    const interrupted = new URL(page.url()).searchParams.get("run")!;
    await expect
      .poll(
        async () =>
          (
            await (await first.request.get(`/api/runs/${interrupted}`)).json()
          ).events?.at(-1)?.node,
      )
      .toBe("validate");
    await stopServer();
    blocker.stdin!.end("release\n");
    if (blocker.exitCode === null) await once(blocker, "exit");
    await startServer();
    await page.reload();
    await expect(page.getByRole("status")).toHaveText("已中断");
    await expect(page.getByText(/服务重启时，这项查询仍在执行/)).toBeVisible();
    await page.getByRole("button", { name: "重新查询" }).click();
    await expect(page.getByRole("status")).toHaveText("已完成");
    expect(new URL(page.url()).searchParams.get("run")).not.toBe(interrupted);
    await expect(
      page.getByRole("navigation", { name: "查询历史" }),
    ).toContainText("已中断");
  } finally {
    if (blocker.exitCode === null && !blocker.stdin?.writableEnded)
      blocker.stdin?.end("release\n");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(root, ".local", "screenshots", "stage4-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(pageErrors).toEqual([]);
});
