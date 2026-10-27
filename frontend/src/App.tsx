import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  clarifyRun,
  createRun,
  followupRun,
  formatValue,
  loadHistory,
  loadRun,
  loadSession,
  retryRun,
  statusLabels,
} from "./api";
import type { DataTable, HistoryItem, Report, Run, Session } from "./api";

const Chart = lazy(() => import("./Chart"));
const examples = [
  "9月各渠道实付金额",
  "9月每天的订单数",
  "9月退款金额与8月比较",
  "9月销售额是多少？",
];
const nodes: Record<string, string> = {
  plan: "解析问题",
  waiting: "等待澄清",
  clarify: "接收补充",
  context: "加载口径",
  draft: "生成 SQL",
  validate: "检查 SQL",
  execute: "只读查询",
  repair: "修复 SQL",
  summarize: "整理结果",
  fail: "停止查询",
  reject: "判断范围",
};
const columns: Record<string, string> = {
  channel: "渠道",
  category: "类别",
  day: "日期",
  value: "指标值",
};
const errors: Record<string, string> = {
  clarification_limit: "补充两次后仍未明确问题。请重新描述指标和日期范围。",
  timeout: "查询超过时间上限，请缩小日期范围。",
  model_key_missing: "当前模型服务尚未配置。",
  provider_changed: "模型配置已改变，请重新查询。",
  budget_exceeded: "本次查询已达到执行预算。",
  process_interrupted: "服务重启时，这项查询仍在执行。你可以从原问题重新查询。",
};

function Table({ table, unit }: { table: DataTable; unit: string }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {table.columns.map((c) => (
              <th key={c}>
                {columns[c] ?? c}
                {c === "value"
                  ? `（${unit.startsWith("fen") ? "元" : "单"}）`
                  : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((v, j) => (
                <td key={j}>
                  {j === row.length - 1 ? formatValue(v, unit) : (v ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {table.rows.length === 0 && (
        <p className="empty-table">该期间没有符合条件的数据。</p>
      )}
    </div>
  );
}
function Result({ report }: { report: Report }) {
  const { metric, plan } = report;
  const grouped = plan.group_by.length > 0;
  const truncated = report.tables.some((t) => t.truncated);
  const unit = metric.unit.startsWith("fen") ? "元" : "单";
  return (
    <section className="result" aria-label="查询结果">
      <div className="section-heading">
        <div>
          <span className="eyebrow">RESULT / 查询结果</span>
          <h2>{metric.label}</h2>
        </div>
        <span className="badge success">
          {truncated ? "结果已截断" : "查询完成"}
        </span>
      </div>
      <p className="metric-definition">{metric.definition}</p>
      <div className="result-summary">
        <div>
          <span className="muted">
            {grouped
              ? plan.top_n
                ? `Top ${plan.top_n} 分组`
                : "分组结果"
              : metric.label}
          </span>
          <div className="metric-value">
            {grouped
              ? `${report.tables[0].rows.length}`
              : formatValue(
                  report.tables[0].rows[0]?.at(-1) ?? null,
                  metric.unit,
                )}
            <span>{grouped ? "组" : unit}</span>
          </div>
        </div>
        <div className="period">
          <span>业务日期 · Asia/Shanghai</span>
          <strong>
            {plan.start_date} → {plan.end_date}
          </strong>
          <small>包含起始日期，不含结束日期</small>
        </div>
      </div>
      {plan.top_n && (
        <p className="notice">
          用户指定 Top {plan.top_n}，这些分组不代表完整总体。
        </p>
      )}
      {truncated && (
        <p className="notice">
          结果超过保护上限，已截断。请缩小范围或分组；不据此计算总体或变化率。
        </p>
      )}
      {report.chart && (
        <>
          <div className="chart-heading">
            {report.chart.type === "line" ? "期间趋势" : "分组对比"}
            <span>单位：{unit}</span>
          </div>
          <Suspense fallback={<p className="muted">正在加载图表…</p>}>
            <Chart data={report.chart} />
          </Suspense>
        </>
      )}
      {report.tables.map((table, i) => (
        <div key={i} className="data-section">
          <h3>
            {i === 0
              ? "数据明细"
              : `基期明细 · ${plan.comparison_start_date} → ${plan.comparison_end_date}`}
          </h3>
          <Table table={table} unit={metric.unit} />
        </div>
      ))}
      {report.comparison.length > 0 && (
        <div className="data-section">
          <h3>与基期比较</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>分组</th>
                  <th>本期（{unit}）</th>
                  <th>基期（{unit}）</th>
                  <th>差额（{unit}）</th>
                  <th>变化率</th>
                </tr>
              </thead>
              <tbody>
                {report.comparison.map((r, i) => (
                  <tr key={i}>
                    <td>{r.dimensions.join(" / ") || "总体"}</td>
                    <td>{formatValue(r.current, metric.unit)}</td>
                    <td>{formatValue(r.baseline, metric.unit)}</td>
                    <td>{formatValue(r.delta, metric.unit)}</td>
                    <td>
                      {r.change_pct === null
                        ? "不可计算"
                        : `${Number(r.change_pct).toFixed(2)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <details className="explanation">
        <summary>指标口径与结果说明</summary>
        <p>{report.explanation}</p>
      </details>
    </section>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [id, setId] = useState(() =>
    new URLSearchParams(location.search).get("run"),
  );
  const [run, setRun] = useState<Run | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [followup, setFollowup] = useState("");
  const [source, setSource] = useState<Run | null>(null);
  const [sourceError, setSourceError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollFailed, setPollFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const questionInput = useRef<HTMLTextAreaElement>(null);
  const selected = useRef(id);
  selected.current = id;
  const sourceId = run?.source_run_id;
  useEffect(() => {
    let live = true;
    setSource(null);
    setSourceError("");
    if (sourceId)
      loadRun(sourceId)
        .then((value) => {
          if (live) setSource(value);
        })
        .catch((e) => {
          if (live) setSourceError(e.message);
        });
    return () => {
      live = false;
    };
  }, [sourceId, refresh]);
  useEffect(() => {
    let live = true;
    loadSession()
      .then(async (value) => {
        if (!live) return;
        setSession(value);
        const result = await loadHistory();
        if (live) setHistory(result.runs);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    if (!session || !id) return;
    let live = true;
    let timer: number;
    setRun(null);
    setAnswer("");
    setFollowup("");
    setError("");
    async function poll() {
      try {
        const result = await loadRun(id!);
        if (!live) return;
        setRun(result);
        setPollFailed(false);
        setError("");
        const items = await loadHistory();
        if (!live) return;
        setHistory(items.runs);
        if (result.status === "running") timer = window.setTimeout(poll, 650);
      } catch (e) {
        if (!live) return;
        setError((e as Error).message);
        setPollFailed(true);
        timer = window.setTimeout(poll, 2000);
      }
    }
    void poll();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [session, id, refresh]);
  function select(next: string | null) {
    selected.current = next;
    setId(next);
    setRun(null);
    setAnswer("");
    setError("");
    setRefresh((v) => v + 1);
    const url = new URL(location.href);
    if (next) url.searchParams.set("run", next);
    else url.searchParams.delete("run");
    window.history.replaceState(null, "", url);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!session || busy || !question.trim()) return;
    setBusy(true);
    setError("");
    try {
      select((await createRun(question.trim(), session.csrf_token)).run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function supplement(event: FormEvent) {
    event.preventDefault();
    if (!run || !session || busy || !answer.trim()) return;
    const current = run.run_id;
    setBusy(true);
    setError("");
    try {
      const result = await clarifyRun(run, answer.trim(), session.csrf_token);
      if (selected.current === current) {
        setRun(result);
        setAnswer("");
        setRefresh((v) => v + 1);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function submitFollowup(event: FormEvent) {
    event.preventDefault();
    if (
      !run ||
      run.status !== "succeeded" ||
      !session ||
      busy ||
      !followup.trim()
    )
      return;
    const current = run.run_id;
    setBusy(true);
    setError("");
    try {
      const result = await followupRun(
        run,
        followup.trim(),
        session.csrf_token,
      );
      if (selected.current === current) select(result.run_id);
    } catch (e) {
      if (selected.current === current) setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function retry() {
    if (!run || !session || busy) return;
    setBusy(true);
    setError("");
    try {
      select((await retryRun(run, session.csrf_token)).run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const expired = run?.clarification_expires_at
    ? Date.parse(run.clarification_expires_at) <= Date.now()
    : false;
  return (
    <div className="shell">
      <aside className="sidebar">
        <a href="/" className="brand" aria-label="ShopLens 首页">
          <span className="brand-icon">S</span>ShopLens
          <span className="brand-dot" />
        </a>
        <p className="brand-caption">看清数据，理解经营</p>
        <button
          className="new-query"
          onClick={() => {
            select(null);
            questionInput.current?.focus();
          }}
        >
          ＋ 新建查询
        </button>
        <div className="history-heading">
          最近查询<span>{history.length}</span>
        </div>
        <nav className="history" aria-label="查询历史">
          {history.length === 0 && (
            <p className="history-empty">你的查询会保存在这里。</p>
          )}
          {history.map((item) => (
            <button
              key={item.run_id}
              className={`history-item ${id === item.run_id ? "selected" : ""}`}
              onClick={() => select(item.run_id)}
            >
              <span className={`status-dot ${item.status}`} />
              <span className="history-text">
                {item.question}
                <small>
                  {item.source_run_id ? "追问 · " : ""}
                  {statusLabels[item.status]}
                </small>
              </span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="demo-mark">DEMO DATA</span>
          <p>
            固定种子的合成电商数据
            <br />
            仅用于演示与学习
          </p>
          <small>历史属于当前浏览器会话 · 保留 7 天访问权</small>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span>
            经营分析 / <strong>查询工作台</strong>
          </span>
          <span className="badge">
            {session?.provider === "deepseek"
              ? "DeepSeek 模型"
              : "离线演示模型"}
          </span>
        </header>
        <main>
          <div className="intro">
            <span className="eyebrow">YOUR DATA, A CLEARER VIEW</span>
            <h1>从一个问题开始。</h1>
            <p>用自然语言查询实付、退款、净收款、订单数和客单价。</p>
          </div>
          <div className="dataset">
            <span className="dataset-icon">◈</span>
            <strong>合成演示数据</strong>
            <span>
              {session
                ? session.data_start
                  ? `${session.data_start} — ${session.data_end_exclusive}（不含，含跨期退款）`
                  : "尚未灌入演示数据"
                : "正在连接…"}
            </span>
            <span className="dataset-reference">
              参考日期 {session?.reference_date ?? "—"} · Asia/Shanghai
            </span>
          </div>
          <form className="question-form" onSubmit={submit}>
            <label htmlFor="question">你想了解什么？</label>
            <textarea
              id="question"
              ref={questionInput}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={2000}
              placeholder="例如：9月各渠道实付金额"
              rows={2}
            />
            <div className="question-bottom">
              <span>
                独立新查询，请写明指标和日期。继续追问请使用结果下方入口。
              </span>
              <button
                className="primary"
                type="submit"
                disabled={!session || busy || !question.trim()}
              >
                {busy ? "正在提交…" : "开始查询 ↗"}
              </button>
            </div>
          </form>
          <div className="examples">
            <span>试试这些</span>
            {examples.map((text) => (
              <button
                key={text}
                onClick={() => {
                  setQuestion(text);
                  questionInput.current?.focus();
                }}
              >
                {text} <span>↗</span>
              </button>
            ))}
          </div>
          {error && (
            <div role="alert" className="error">
              {error}
              {pollFailed && <span> 页面会继续尝试连接。</span>}
              {!session && (
                <button onClick={() => location.reload()}>重新连接</button>
              )}
            </div>
          )}
          {id && !run && !error && (
            <div className="state-panel">正在读取查询…</div>
          )}
          {run && (
            <div className="run-heading">
              <p>{run.question}</p>
              <span
                className={`badge ${run.status === "succeeded" ? "success" : ""}`}
                role="status"
              >
                {statusLabels[run.status]}
              </span>
            </div>
          )}
          {run?.source_run_id && (
            <details className="followup-source" open>
              <summary>基于查询：{run.source_question}</summary>
              <button onClick={() => select(run.source_run_id)}>
                查看来源查询
              </button>
              {source?.run_id === run.source_run_id && source.report && (
                <Result report={source.report} />
              )}
              {!source && !sourceError && <p>正在读取来源结果…</p>}
              {sourceError && <p role="alert">{sourceError}</p>}
            </details>
          )}
          {run?.status === "running" && (
            <section className="state-panel">
              <span className="spinner" />
              <h2>正在分析你的问题</h2>
              <p>
                {run.events?.length
                  ? `${nodes[run.events.at(-1)!.node] ?? run.events.at(-1)!.node}已完成`
                  : "准备查询流程"}{" "}
                · 结果会自动更新
              </p>
            </section>
          )}
          {run?.status === "awaiting_clarification" && (
            <section className="state-panel clarify">
              <span className="eyebrow">再补充一点</span>
              <h2>{run.plan?.clarification_question}</h2>
              {expired ? (
                <>
                  <p>这次澄清已超过 24 小时，请重新查询。</p>
                  <button className="primary" onClick={retry} disabled={busy}>
                    重新查询
                  </button>
                </>
              ) : (
                <form onSubmit={supplement}>
                  <label htmlFor="answer">补充说明</label>
                  <input
                    id="answer"
                    value={answer}
                    onChange={(e) => setAnswer(e.target.value)}
                    maxLength={2000}
                    placeholder="例如：实付金额"
                  />
                  <button
                    className="primary"
                    type="submit"
                    disabled={busy || !answer.trim()}
                  >
                    提交补充
                  </button>
                  <small>24 小时内可提交，最多补充两次。</small>
                </form>
              )}
            </section>
          )}
          {run?.status === "succeeded" && run.report && (
            <div className="current-result">
              <Result report={run.report} />
              <form className="followup-form" onSubmit={submitFollowup}>
                <h2>基于这条结果继续追问</h2>
                <p>沿用这条结果的条件，你明确修改的条件优先。</p>
                <label htmlFor="followup">继续追问内容</label>
                <input
                  id="followup"
                  value={followup}
                  onChange={(e) => setFollowup(e.target.value)}
                  maxLength={2000}
                  placeholder="例如：那各渠道呢？或改成2026年8月"
                />
                <button
                  className="primary"
                  type="submit"
                  disabled={busy || !session || !followup.trim()}
                >
                  提交追问
                </button>
              </form>
            </div>
          )}
          {run &&
            ["failed", "interrupted", "rejected"].includes(run.status) && (
              <section className="state-panel">
                <h2>{statusLabels[run.status]}</h2>
                <p>
                  {run.status === "rejected"
                    ? run.plan?.unsupported_reason
                    : (errors[run.error_code ?? ""] ??
                      "查询未完成，请重新查询或调整问题。")}
                </p>
                {run.status !== "rejected" && (
                  <button className="primary" onClick={retry} disabled={busy}>
                    重新查询
                  </button>
                )}
              </section>
            )}
          {run && (
            <details className="trace">
              <summary>
                查看 SQL 与执行过程
                <span>
                  {run.model_calls ?? 0} 次模型调用 · {run.active_ms ?? 0} ms
                </span>
              </summary>
              <div className="trace-body">
                {run.sqls?.map((sql, i) => (
                  <pre key={i}>{sql}</pre>
                ))}
                {!run.sqls?.length && <p>尚未生成 SQL。</p>}
                <ol>
                  {run.events?.map((event, i) => (
                    <li key={i}>
                      <span>{nodes[event.node] ?? event.node}</span>
                      <small>
                        {event.elapsed_ms} ms
                        {event.error_code ? ` · ${event.error_code}` : ""}
                      </small>
                    </li>
                  ))}
                </ol>
                <small>
                  修复 {run.repair_count ?? 0} 次 ·{" "}
                  {run.provider ?? session?.provider} /{" "}
                  {run.model_id ?? "待分配"} · Token{" "}
                  {run.tokens?.total_tokens ?? "不可用"}
                </small>
              </div>
            </details>
          )}
          {!id && (
            <section className="welcome">
              <span className="welcome-symbol">↗</span>
              <h2>让每个数字都有依据。</h2>
              <p>
                查询结果包含指标口径、数据明细和执行过程。
                <br />
                信息不足时，先向你确认，再继续分析。
              </p>
              <div>
                <span>01 明确问题</span>
                <span>02 受限查询</span>
                <span>03 查看结果</span>
              </div>
            </section>
          )}
          <footer className="main-footer">
            ShopLens · 电商经营数据分析助手
            <span>
              合成数据 /{" "}
              {session?.provider === "deepseek" ? "真实模型" : "离线假模型"}
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}
