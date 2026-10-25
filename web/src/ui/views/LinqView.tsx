import { useCallback, useEffect, useState } from "react";
import {
  getLinq,
  getMetrics,
  linqConnect,
  linqDisconnect,
  setLinqDevice,
} from "../../infrastructure/api";
import {
  deviceSummaries,
  linqHeadline,
  measureRows,
  measureTotals,
  parseLinq,
  parseProgram,
  pipelineNodes,
  type LinqStatus,
} from "../../domain/linq";
import { DEVICE_LABELS, parseMetrics } from "../../domain/metrics";
import type { Metrics, MetricsDevice } from "../../domain/types";

const POLL_MS = 3000;
const BUSY_POLL_MS = 1500;

/** Раздел «LinQ»: подключение, путь кадра до ускорителя, программы .tpu, сравнение устройств и замеры. */
export default function LinqView({ readOnly = false }: { readOnly?: boolean }) {
  const [status, setStatus] = useState<LinqStatus | null>(null);
  const [metrics, setMetrics] = useState<Metrics[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const parsed = parseLinq(await getLinq());
      if (parsed) setStatus(parsed);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
    try {
      setMetrics(parseMetrics(await getMetrics()));
    } catch {
      setMetrics([]);
    }
    setNow(Date.now());
  }, []);

  const busy = status?.busy ?? false;
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, busy ? BUSY_POLL_MS : POLL_MS);
    return () => clearInterval(timer);
  }, [refresh, busy]);

  async function act(action: () => Promise<unknown>) {
    setPending(true);
    try {
      const parsed = parseLinq(await action());
      if (parsed) setStatus(parsed);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (!status) return <p className={error ? "error" : "muted"}>{error || "Загрузка…"}</p>;
  const head = linqHeadline(status);
  const rows = measureRows(metrics, now);
  const totals = measureTotals(metrics, now);
  const devices = deviceSummaries(metrics, now);
  const nodes = pipelineNodes(status, metrics, now);
  const programs = (status.report?.programs ?? []).map(parseProgram);
  const connected = status.state === "connected";
  const maxFps = Math.max(1, ...devices.map((d) => d.fps));
  const maxRowFps = Math.max(1, ...metrics.map((m) => m.fps));
  const tone = head.tone === "ok" ? "connected" : head.tone === "warn" ? "fallback" : "offline";

  return (
    <section className="linq">
      <div className={`linq-hero linq-${tone}`}>
        <div className="linq-hero-text">
          <span className="linq-eyebrow">Ускоритель инференса</span>
          <h2>
            LinQ <small>H1M</small>
          </h2>
          <span className={`linq-badge linq-${tone}`}>
            <span className="linq-dot" />
            {head.text}
            <small>{status.host}</small>
          </span>
        </div>
        <div className="linq-controls">
          <label className="filter">
            <span>Устройство инференса</span>
            <select
              value={status.device}
              disabled={readOnly || pending}
              onChange={(e) => act(() => setLinqDevice(e.target.value))}
            >
              {status.devices.map((d: MetricsDevice) => (
                <option key={d} value={d}>
                  {DEVICE_LABELS[d]}
                </option>
              ))}
            </select>
          </label>
          <div className="actions">
            <button
              type="button"
              className="linq-primary"
              disabled={readOnly || pending || busy || connected}
              onClick={() => act(linqConnect)}
            >
              {busy && !connected ? "Подключаю…" : "Подключить"}
            </button>
            <button
              type="button"
              disabled={readOnly || pending || busy || !connected}
              onClick={() => act(linqDisconnect)}
            >
              Отключить
            </button>
          </div>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {readOnly && <p className="muted">Подключением управляет администратор.</p>}

      <div className="card-panel">
        <h3>Путь кадра</h3>
        <ol className="linq-flow">
          {nodes.map((n) => (
            <li key={n.id} className={`flow-node tone-${n.tone}`}>
              <span className="flow-dot" />
              <b>{n.title}</b>
              <span>{n.note}</span>
            </li>
          ))}
        </ol>
      </div>

      <div className="kpis linq-kpis">
        <div className="kpi">
          <b>{totals ? totals.fps.toFixed(1) : "—"}</b>
          <span>кадров/с суммарно</span>
        </div>
        <div className="kpi">
          <b>{totals ? `${Math.round(totals.inferenceMs)} мс` : "—"}</b>
          <span>инференс в среднем</span>
        </div>
        <div className="kpi">
          <b>{totals ? totals.cameras : 0}</b>
          <span>камер с живыми замерами</span>
        </div>
        <div className="kpi">
          <b>{programs.length}</b>
          <span>программ .tpu на стенде</span>
        </div>
      </div>

      <div className="split-grid">
        <div className="card-panel">
          <h3>Программы на ускорителе</h3>
          {programs.length ? (
            <ul className="prog-list">
              {programs.map((p) => (
                <li key={p.file} className={p.ours ? "prog ours" : "prog"}>
                  <div className="prog-name">
                    <b>{p.model}</b>
                    <span className={p.ours ? "chip ok" : "chip-tag"}>
                      {p.ours ? "наша модель СИЗ" : "образец"}
                    </span>
                  </div>
                  <div className="prog-meta">
                    {p.platform && <span className="chip-tag">{p.platform}</span>}
                    {p.preset && <span className="chip-tag">{p.preset}</span>}
                    {p.batch && <span className="chip-tag">батч {p.batch}</span>}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty-note">
              {status.report
                ? "На стенде нет скомпилированных программ."
                : "Стенд ещё не проверен: подключите LinQ."}
            </p>
          )}
          {status.report && (
            <details className="zero-details">
              <summary>
                Окружение стенда · проверено {status.report.checked_at?.replace("T", " ") ?? "—"}
              </summary>
              <p className="chips">
                {Object.entries(status.report.modules).map(([name, ok]) => (
                  <span key={name} className={ok ? "chip ok" : "chip warn"}>
                    {name}: {ok ? "есть" : "нет"}
                  </span>
                ))}
              </p>
            </details>
          )}
        </div>

        <div className="card-panel">
          <h3>Сравнение устройств</h3>
          {devices.length ? (
            <ul className="dev-bars">
              {devices.map((d) => (
                <li key={d.device} className={d.device === "linq" ? "dev linq" : "dev"}>
                  <div className="dev-head">
                    <b>{d.label}</b>
                    <span>
                      {d.fps.toFixed(1)} к/с · {Math.round(d.inferenceMs)} мс · камер: {d.cameras}
                    </span>
                  </div>
                  <div className="bar">
                    <span style={{ width: `${Math.max(3, (d.fps / maxFps) * 100)}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty-note">Свежих замеров нет: воркеры не запущены или молчат.</p>
          )}
        </div>
      </div>

      {status.bench && (
        <div className="card-panel">
          <h3>
            Замеры на ускорителе <small className="muted">· {status.bench.program}</small>
          </h3>
          <div className="kpis linq-kpis">
            {status.bench.latency && (
              <div className="kpi">
                <b>{Math.round(status.bench.latency.p50)} мс</b>
                <span>задержка кадра, медиана (p95 {Math.round(status.bench.latency.p95)} мс)</span>
              </div>
            )}
            {status.bench.fps.length > 0 && (
              <div className="kpi">
                <b>{Math.max(...status.bench.fps.map((f) => f.fps)).toFixed(1)}</b>
                <span>кадров/с в тихое время стенда</span>
              </div>
            )}
            {status.bench.loaded && (
              <div className="kpi">
                <b>{Math.round(status.bench.loaded.p50)} мс</b>
                <span>медиана под общей нагрузкой стенда ({status.bench.loaded.fps.toFixed(1)} кадр/с)</span>
              </div>
            )}
            {status.bench.best && (
              <div className="kpi">
                <b>F1 {status.bench.best.f1.toFixed(2)}</b>
                <span>
                  совпадение с FP32 при пороге {status.bench.best.conf}: полнота{" "}
                  {Math.round(status.bench.best.recall * 100)} %, точность{" "}
                  {Math.round(status.bench.best.precision * 100)} %
                </span>
              </div>
            )}
          </div>
          {status.bench.fps.length > 0 && (
            <ul className="dev-bars">
              {status.bench.fps.map((f) => (
                <li key={f.inflight} className="dev linq">
                  <div className="dev-head">
                    <b>{f.inflight} в полёте</b>
                    <span>{f.fps.toFixed(1)} кадров/с</span>
                  </div>
                  <div className="bar">
                    <span
                      style={{
                        width: `${Math.max(3, (f.fps / Math.max(...status.bench!.fps.map((x) => x.fps))) * 100)}%`,
                      }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="muted">
            Замер самой программы на стенде, не живого потока.
            {status.bench.calibrationFrames
              ? ` Калибровка INT8 на ${status.bench.calibrationFrames} кадрах: чем больше кадров, тем ближе к FP32.`
              : ""}
          </p>
        </div>
      )}

      <div className="card-panel">
        <h3>Замеры по камерам</h3>
        {rows.length > 0 ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Камера</th>
                  <th>Устройство</th>
                  <th>FPS</th>
                  <th>Инференс</th>
                  <th>Сквозная задержка</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.camera} className={r.stale ? "stale" : ""}>
                    <td>{r.camera}</td>
                    <td>
                      <span className={r.onLinq ? "chip-tag on-linq" : "chip-tag"}>{r.device}</span>
                      {r.stale ? " (устарело)" : ""}
                    </td>
                    <td className="fps-cell">
                      {r.fps}
                      <span className="mini-bar">
                        <span style={{ width: `${Math.min(100, (Number(r.fps) / maxRowFps) * 100)}%` }} />
                      </span>
                    </td>
                    <td>{r.inference}</td>
                    <td>{r.e2e}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-note">Свежих замеров нет: воркеры не запущены или молчат.</p>
        )}
      </div>

      {status.log.length > 0 && (
        <details className="card-panel">
          <summary>Журнал подключения</summary>
          <pre className="linq-log">{status.log.join("\n")}</pre>
        </details>
      )}
      <p className="muted">Логины и пароли хранятся на машине бэкенда и в браузер не передаются.</p>
    </section>
  );
}
