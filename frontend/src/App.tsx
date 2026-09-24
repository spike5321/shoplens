import { useCallback, useEffect, useState } from "react";

import { apiBase, fetchHealth, fetchReady } from "./api";
import type { HealthzResponse, ReadyzResponse } from "./api";

/**
 * 阶段 0 的最小连通页。
 *
 * 它只做一件事：证明「前端 -> 后端 -> PostgreSQL」这条链路是真的通的，
 * 并且把「哪些能力已验证、哪些还没有」如实展示出来。
 * 不包含任何查数功能 —— 业务功能从阶段 4 才开始。
 */

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; health: HealthzResponse; ready: ReadyzResponse }
  | { kind: "error"; message: string };

const DEMO_REFERENCE_DATE = "2026-09-30";
const DATA_RANGE = "2026-07-01 ~ 2026-09-30";

export default function App() {
  const [state, setState] = useState<LoadState>({ kind: "idle" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const health = await fetchHealth();
      const ready = await fetchReady();
      setState({ kind: "ok", health, ready });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setState({ kind: "error", message });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1 className="page__title">ShopLens</h1>
          <p className="page__subtitle">
            基于 LangChain + LangGraph 的电商经营数据分析助手
          </p>
        </div>
        <button className="btn" onClick={() => void load()} disabled={state.kind === "loading"}>
          {state.kind === "loading" ? "检测中…" : "重新检测"}
        </button>
      </header>

      {/* 合成数据必须显式标注，不能让看的人以为是真实业务数据 */}
      <div className="banner">
        <strong>合成演示数据</strong>
        <span className="banner__sep">·</span>
        <span>数据范围 {DATA_RANGE}</span>
        <span className="banner__sep">·</span>
        <span>
          演示参考日期 <code>{DEMO_REFERENCE_DATE}</code>
        </span>
      </div>

      <main className="grid">
        <section className="card">
          <h2 className="card__title">后端连通性</h2>
          {state.kind === "loading" && <p className="muted">正在请求 {apiBase} …</p>}
          {state.kind === "error" && (
            <>
              <p className="pill pill--bad">未连通</p>
              <p className="muted">
                无法访问 {apiBase}。请确认后端已启动：
                <br />
                <code>uvicorn app.main:app --reload --port 8000</code>
              </p>
              <p className="error">{state.message}</p>
            </>
          )}
          {state.kind === "ok" && (
            <>
              <p className="pill pill--good">已连通</p>
              <dl className="kv">
                <dt>服务</dt>
                <dd>{state.health.service}</dd>
                <dt>版本</dt>
                <dd>{state.health.version}</dd>
                <dt>运行环境</dt>
                <dd>{state.health.env}</dd>
                <dt>模型服务商</dt>
                <dd>
                  {state.health.model_provider}
                  {state.health.model_provider === "offline" && (
                    <span className="tag">离线假模型</span>
                  )}
                </dd>
                <dt>真实模型 Key</dt>
                <dd>
                  {state.health.real_model_key_configured ? (
                    <span className="pill pill--good">已配置</span>
                  ) : (
                    <span className="pill pill--warn">未配置（真实模型检查未验证）</span>
                  )}
                </dd>
              </dl>
            </>
          )}
        </section>

        <section className="card">
          <h2 className="card__title">PostgreSQL 连通性</h2>
          {state.kind === "loading" && <p className="muted">正在探测数据库…</p>}
          {state.kind === "error" && <p className="muted">后端未连通，无法探测数据库。</p>}
          {state.kind === "ok" && <DatabasePanel ready={state.ready} />}
        </section>
      </main>

      <footer className="page__footer">
        阶段 0 完成范围：项目骨架、healthz / readyz、LangChain 工具与 LangGraph 条件边学习样例。
        <br />
        查数、澄清、图表、持久化均为<strong>尚未开始</strong>。
      </footer>
    </div>
  );
}

function DatabasePanel({ ready }: { ready: ReadyzResponse }) {
  const db = ready.database;

  if (db.status === "ok") {
    return (
      <>
        <p className="pill pill--good">已连通（{db.latency_ms} ms）</p>
        <dl className="kv">
          <dt>地址</dt>
          <dd>
            {db.host}:{db.port}
          </dd>
          <dt>库名</dt>
          <dd>{db.dbname}</dd>
          <dt>连接身份</dt>
          <dd>
            <code>{db.dsn}</code>
          </dd>
          <dt>服务端版本</dt>
          <dd>{db.server_version}</dd>
          <dt>会话时区</dt>
          <dd>{db.timezone ?? "—"}</dd>
        </dl>
      </>
    );
  }

  if (db.status === "unreachable") {
    return (
      <>
        <p className="pill pill--bad">未连通</p>
        <p className="muted">
          后端进程正常，但连不上 PostgreSQL。这是<strong>如实报告</strong>，不是页面坏了。
        </p>
        <dl className="kv">
          <dt>地址</dt>
          <dd>
            {db.host}:{db.port}
          </dd>
          <dt>错误</dt>
          <dd className="error">{db.error}</dd>
        </dl>
      </>
    );
  }

  return <p className="muted">状态未知。</p>;
}
