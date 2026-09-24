/**
 * 后端接口的最小类型化封装。
 *
 * 阶段 0 只有两个运维探针；业务接口（创建查询、读取结果、提交澄清、历史记录）
 * 会在阶段 4 加入，届时继续沿用这里的类型化封装方式。
 */

const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

export interface HealthzResponse {
  status: string;
  service: string;
  version: string;
  env: string;
  model_provider: "offline" | "deepseek";
  real_model_key_configured: boolean;
}

export interface DatabaseStatus {
  host: string;
  port: number;
  dbname: string;
  /** 已经去掉密码的连接串，可以安全展示 */
  dsn: string;
  status: "ok" | "unreachable" | "unknown";
  latency_ms: number | null;
  server_version: string | null;
  timezone?: string;
  error: string | null;
}

export interface ReadyzResponse {
  service: string;
  database: DatabaseStatus;
}

async function getJson<T>(path: string, timeoutMs = 5000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(`${API_BASE}${path}`, { signal: controller.signal });
    if (!resp.ok) {
      throw new Error(`${path} 返回 HTTP ${resp.status}`);
    }
    return (await resp.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

export const apiBase = API_BASE;

export function fetchHealth(): Promise<HealthzResponse> {
  return getJson<HealthzResponse>("/healthz");
}

export function fetchReady(): Promise<ReadyzResponse> {
  return getJson<ReadyzResponse>("/readyz", 8000);
}
