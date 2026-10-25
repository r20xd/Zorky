import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteDemoVideo,
  getDemoVideos,
  playDemoVideo,
  stopDemoVideo,
  uploadDemoVideo,
} from "../../infrastructure/api";
import {
  fileProblem,
  nextVideoId,
  parseDemoState,
  sizeLabel,
  validCameraId,
  type DemoState,
} from "../../domain/demoVideos";
import type { Camera } from "../../domain/types";

/** Демо-видео: выбрать свои ролики и пустить по кругу как камеры (только демо-режим, admin). */
export default function DemoVideosView({ cameras, onCamerasChange }: { cameras: Camera[]; onCamerasChange: () => void }) {
  const [state, setState] = useState<DemoState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ids, setIds] = useState<Record<string, string>>({});
  const [detect, setDetect] = useState<Record<string, boolean>>({});
  const input = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const parsed = parseDemoState(await getDemoVideos());
      if (parsed) setState(parsed);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      const parsed = parseDemoState(await action());
      if (parsed) setState(parsed);
      setError("");
      onCamerasChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const problem = fileProblem(file.name, file.size);
    if (problem) return setError(problem);
    await run(() => uploadDemoVideo(file));
  }

  if (!state) return <p className={error ? "error" : "muted"}>{error || "Загрузка…"}</p>;
  if (!state.enabled)
    return (
      <section className="demo-videos">
        <h2>Демо-видео</h2>
        <p className="muted">Раздел работает только в демо-режиме (запуск через ярлык «Демо»).</p>
      </section>
    );

  const taken = [...cameras.map((c) => c.id), ...state.players.map((p) => p.camera_id)];
  const playing = new Map(state.players.map((p) => [p.video, p.camera_id]));
  const idFor = (video: string) => ids[video] ?? playing.get(video) ?? nextVideoId(taken);

  return (
    <section className="demo-videos">
      <div className="analytics-head">
        <h2>Демо-видео</h2>
        <span className="muted">Файлы лежат на этом компьютере: {state.folder}</span>
      </div>
      <p className="muted">
        Выберите свои ролики — ролик запускается как камера по кругу. С галкой «Распознавать нарушения» его читает
        воркер с моделью СИЗ (рамки и нарушения в «Мониторинге» и журнале); воркер запускает scripts/demo.bat, без
        него кадров не будет. Без галки показывается только картинка.
      </p>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <input ref={input} type="file" accept="video/*,.mkv" hidden onChange={pick} />
        <button type="button" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Подождите…" : "Выбрать видео с компьютера"}
        </button>
      </div>
      {state.videos.length === 0 ? (
        <p className="muted">Видео пока нет. Нажмите «Выбрать видео с компьютера».</p>
      ) : (
        <ul className="demo-list">
          {state.videos.map((v) => {
            const camera = playing.get(v.name);
            const id = idFor(v.name);
            return (
              <li key={v.name} className="card-panel">
                <div>
                  <b>{v.name}</b> <span className="muted">{sizeLabel(v.mb)}</span>
                  {camera && <span className="chip ok"> идёт по кругу как «{camera}»</span>}
                </div>
                <div className="actions">
                  <label className="filter">
                    <span>id камеры</span>
                    <input
                      value={id}
                      disabled={!!camera}
                      onChange={(e) => setIds({ ...ids, [v.name]: e.target.value.trim() })}
                    />
                  </label>
                  {!camera && (
                    <label className="filter" title="Файл читает воркер с моделью СИЗ: рамки и нарушения (медленнее)">
                      <span>
                        <input
                          type="checkbox"
                          checked={detect[v.name] ?? true}
                          onChange={(e) => setDetect({ ...detect, [v.name]: e.target.checked })}
                        />{" "}
                        Распознавать нарушения
                      </span>
                    </label>
                  )}
                  {camera ? (
                    <button type="button" disabled={busy} onClick={() => run(() => stopDemoVideo(camera))}>
                      Остановить
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled={busy || !validCameraId(id)}
                      onClick={() => run(() => playDemoVideo(id, v.name, detect[v.name] ?? true))}
                    >
                      Пустить по кругу
                    </button>
                  )}
                  <button type="button" disabled={busy || !!camera} onClick={() => run(() => deleteDemoVideo(v.name))}>
                    Удалить
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
