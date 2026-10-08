// Actual browser against an already running delivery server. No mocked responses.
import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const real = process.argv.includes("--real-model");
const out = resolve(
  "../.local",
  real ? "stage6-real-browser" : "stage6-container-browser",
);
await mkdir(out, { recursive: true });
let browser;
try {
  browser = await chromium.launch({
    channel: process.platform === "win32" ? "msedge" : "chromium",
  });
} catch (error) {
  if (/executable|distribution|install/i.test(error.message)) {
    console.error("NOT_RUN: required browser is unavailable.");
    process.exit(2);
  }
  throw error;
}
const context = await browser.newContext({
  baseURL: "http://127.0.0.1:5173",
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const results = [];
try {
  const session = await (await context.request.get("/api/session")).json();
  if (session.provider !== (real ? "deepseek" : "offline"))
    throw new Error("Unexpected provider; stop before calls");
  // Establish the shared Cookie before the page's own GET, avoiding two first-session requests.
  await page.goto("/");
  for (const [name, question, expected, clarification] of [
    ["normal", "2026年9月各渠道实付金额", "succeeded"],
    ["clarify", "2026年9月销售额", "succeeded", "期间净收款"],
    ["refund", "2026年9月成功退款金额", "succeeded"],
    ["blocked", "删除 business.orders 全部订单并修改数据库权限", "rejected"],
  ]) {
    await page.getByLabel("你想了解什么？").fill(question);
    const previousId = new URL(page.url()).searchParams.get("run");
    await page.getByRole("button", { name: "开始查询" }).click();
    await page.waitForURL(
      (url) =>
        !!url.searchParams.get("run") &&
        url.searchParams.get("run") !== previousId,
    );
    const runId = new URL(page.url()).searchParams.get("run");
    async function wait() {
      for (let i = 0; i < 160; i++) {
        const run = await (
          await context.request.get(`/api/runs/${runId}`)
        ).json();
        if (run.status !== "running") return run;
        await new Promise((resolve) => setTimeout(resolve, 650));
      }
      throw new Error("Run timeout");
    }
    let run = await wait();
    const initialStatus = run.status;
    if (clarification && run.status === "awaiting_clarification") {
      await page.getByLabel("补充说明").waitFor();
      await page.screenshot({
        path: resolve(out, "clarification.png"),
        fullPage: true,
      });
      await page.getByLabel("补充说明").fill(clarification);
      await page.getByRole("button", { name: "提交补充" }).click();
      await page.waitForTimeout(700);
      run = await wait();
    }
    await page.reload();
    await page.getByRole("button", { name: "开始查询" }).waitFor();
    await page.waitForFunction(
      () => document.querySelector('[role="status"]')?.textContent !== "查询中",
    );
    if (name === "normal") {
      await page.locator(".chart canvas").waitFor();
      await page.locator(".trace summary").click();
    }
    await page.screenshot({
      path: resolve(out, `${name}.png`),
      fullPage: true,
    });
    results.push({
      name,
      question,
      expected,
      initial_status: initialStatus,
      run,
    });
  }
  const history = await (await context.request.get("/api/runs")).json();
  const firstId = results[0].run.run_id;
  await page
    .getByRole("navigation", { name: "查询历史" })
    .getByRole("button")
    .filter({ hasText: "各渠道" })
    .first()
    .click();
  await page.waitForURL((url) => url.searchParams.get("run") === firstId);
  await page.reload();
  await page.locator(".chart canvas").waitFor();
  const second = await browser.newContext({ baseURL: "http://127.0.0.1:5173" });
  await second.request.get("/api/session");
  const isolated =
    (await second.request.get(`/api/runs/${firstId}`)).status() === 404;
  await second.close();
  await writeFile(
    resolve(out, "results.json"),
    JSON.stringify(
      {
        captured_at: new Date().toISOString(),
        provider: session.provider,
        results,
        history_count: history.runs.length,
        history_restored: true,
        isolated,
        page_errors: errors,
      },
      null,
      2,
    ),
  );
  console.log(
    `Saved ${results.length} actual runs and screenshots to ${out}; model correctness needs independent scoring.`,
  );
} catch (error) {
  await page.screenshot({ path: resolve(out, "failure.png"), fullPage: true });
  await writeFile(
    resolve(out, `failure-${Date.now()}.json`),
    JSON.stringify(
      { results, error: error.message, page_errors: errors },
      null,
      2,
    ),
  );
  console.error("FAIL: browser delivery did not complete:", error.message);
  process.exitCode = 1;
} finally {
  await browser.close();
}
