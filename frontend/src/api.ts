const BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
export interface Session {
  csrf_token: string;
  provider: "offline" | "deepseek";
  reference_date: string;
  business_timezone: string;
  data_start: string | null;
  data_end_exclusive: string | null;
  synthetic: boolean;
}
export type Status =
  | "running"
  | "awaiting_clarification"
  | "succeeded"
  | "failed"
  | "rejected"
  | "interrupted";
export interface HistoryItem {
  run_id: string;
  question: string;
  status: Status;
  created_at: string;
}
export interface DataTable {
  columns: string[];
  rows: (string | null)[][];
  truncated: boolean;
  requested_top_n: number | null;
}
export interface ChartData {
  type: "line" | "bar";
  unit: string;
  labels: string[];
  values: (string | null)[];
}
export interface Report {
  metric: { id: string; label: string; definition: string; unit: string };
  plan: {
    start_date: string;
    end_date: string;
    comparison_start_date: string | null;
    comparison_end_date: string | null;
    top_n: number | null;
    group_by: string[];
  };
  tables: DataTable[];
  chart: ChartData | null;
  comparison: {
    dimensions: string[];
    current: string | null;
    baseline: string | null;
    delta: string | null;
    change_pct: string | null;
  }[];
  explanation: string;
}
export interface Run extends HistoryItem {
  revision: number;
  updated_at: string;
  clarification_expires_at: string | null;
  plan: { clarification_question?: string; unsupported_reason?: string } | null;
  report: Report | null;
  sqls: string[] | null;
  events:
    | {
        node: string;
        elapsed_ms: number;
        model_calls: number;
        error_code: string | null;
      }[]
    | null;
  error_code: string | null;
  provider: string | null;
  model_id: string | null;
  model_calls: number | null;
  repair_count: number | null;
  active_ms: number | null;
  tokens: { total_tokens: number } | null;
  clarifications: string[] | null;
}
async function json<T>(path: string, body?: object, csrf?: string): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(`${BASE}/api${path}`, {
      method: body ? "POST" : "GET",
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
      headers: body
        ? { "Content-Type": "application/json", "X-CSRF-Token": csrf ?? "" }
        : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const value = await response.json();
    if (!response.ok)
      throw new Error(
        typeof value.detail === "string"
          ? value.detail
          : "请求内容不符合要求，请检查输入。",
      );
    return value as T;
  } catch (error) {
    if (
      error instanceof TypeError ||
      (error instanceof DOMException && error.name === "AbortError")
    )
      throw new Error("暂时无法连接查询服务，请稍后刷新或重试。");
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}
// StrictMode 重放 effect 时共用建会话请求，避免两个 Cookie 相互覆盖。
let sessionPromise: Promise<Session> | undefined;
export function loadSession() {
  sessionPromise ??= json<Session>("/session").catch((error) => {
    sessionPromise = undefined;
    throw error;
  });
  return sessionPromise;
}
export const loadHistory = () =>
  json<{ runs: HistoryItem[]; limit: number }>("/runs");
export const loadRun = (id: string) =>
  json<Run>(`/runs/${encodeURIComponent(id)}`);
export const createRun = (question: string, csrf: string) =>
  json<Run>("/runs", { question }, csrf);
export const clarifyRun = (run: Run, text: string, csrf: string) =>
  json<Run>(
    `/runs/${run.run_id}/clarify`,
    { text, revision: run.revision },
    csrf,
  );
export const retryRun = (run: Run, csrf: string) =>
  json<Run>(`/runs/${run.run_id}/retry`, {}, csrf);
export const statusLabels: Record<Status, string> = {
  running: "查询中",
  awaiting_clarification: "待补充",
  succeeded: "已完成",
  failed: "查询失败",
  rejected: "暂不支持",
  interrupted: "已中断",
};
export function formatValue(value: string | null, unit: string): string {
  if (value === null) return "不可计算";
  if (unit !== "fen" && unit !== "fen_per_order")
    return BigInt(value).toLocaleString("zh-CN");
  const negative = value.startsWith("-");
  const [integer, fraction = ""] = value.replace(/^-/, "").split(".");
  const fen = BigInt(integer) + (Number(fraction[0] ?? "0") >= 5 ? 1n : 0n);
  return `${negative && fen !== 0n ? "−" : ""}${(fen / 100n).toLocaleString("zh-CN")}.${String(fen % 100n).padStart(2, "0")}`;
}
