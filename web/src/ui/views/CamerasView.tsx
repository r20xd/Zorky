import { useEffect, useState } from "react";
import { deleteCamera, getMetrics, listCameras, saveCamera } from "../../infrastructure/api";
import { buildCameraBody } from "../../domain/areas";
import { cameraState, maskSource, validateCamera } from "../../domain/cameras";
import { parseMetrics } from "../../domain/metrics";
import { BROWSER_CAM_ID, BROWSER_CAM_NAME } from "../../domain/browserCamera";
import type { Camera, Metrics } from "../../domain/types";
import BrowserCamera from "../components/BrowserCamera";

const EMPTY: Camera = { id: "", name: "", source: "" };

export default function CamerasView({
  cameras,
  onChange,
}: {
  cameras: Camera[];
  onChange: (list: Camera[]) => void;
}) {
  const [metrics, setMetrics] = useState<Metrics[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [form, setForm] = useState<Camera>(EMPTY);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () =>
      getMetrics()
        .then((raw) => setMetrics(parseMetrics(raw)))
        .catch(() => setMetrics([]));
    load();
    const timer = setInterval(() => {
      setNow(Date.now());
      void load();
    }, 3000);
    return () => clearInterval(timer);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const problem = validateCamera(form);
    if (problem) return setError(problem);
    try {
      await saveCamera(buildCameraBody(form));
      onChange(await listCameras());
      setForm(EMPTY);
      setEditing(false);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove(camera: Camera) {
    if (!window.confirm(`Удалить камеру «${camera.name}» (${camera.id})?`)) return;
    try {
      await deleteCamera(camera.id);
      onChange(cameras.filter((c) => c.id !== camera.id));
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <section className="cams">
      {error && <p className="error">{error}</p>}
      <table className="cams-table">
        <thead>
          <tr>
            <th>Состояние</th>
            <th>id</th>
            <th>Название</th>
            <th>Участок</th>
            <th>Адрес</th>
            <th>FPS</th>
            <th>Задержка</th>
            <th>
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {cameras.map((camera) => {
            const st = cameraState(
              metrics.find((m) => m.camera_id === camera.id),
              now,
            );
            return (
              <tr key={camera.id}>
                <td>
                  <span className={st.online ? "badge online" : "badge offline"}>
                    {st.online ? "в сети" : "не в сети"}
                  </span>
                </td>
                <td>{camera.id}</td>
                <td>{camera.name}</td>
                <td>{camera.area ?? "—"}</td>
                <td>{maskSource(camera.source)}</td>
                <td>{st.fps === null ? "—" : st.fps.toFixed(1)}</td>
                <td>{st.delayMs === null ? "—" : `${Math.round(st.delayMs)} мс`}</td>
                <td>
                  <button
                    onClick={() => {
                      setForm({ ...camera });
                      setEditing(true);
                    }}
                  >
                    Править
                  </button>{" "}
                  <button onClick={() => remove(camera)}>Удалить</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h2>Камера этого устройства</h2>
      <p className="muted">
        Браузер спросит разрешение на камеру и будет присылать кадры на сервер (камера «{BROWSER_CAM_ID}»).
      </p>
      <BrowserCamera
        cameraId={BROWSER_CAM_ID}
        cameraName={BROWSER_CAM_NAME}
        onSent={(count) => {
          if (count === 1) void listCameras().then(onChange).catch(() => undefined);
        }}
      />
      <h2>{editing ? "Правка камеры" : "Добавить камеру"}</h2>
      <form className="cams-form" onSubmit={submit}>
        <input
          placeholder="id"
          value={form.id}
          disabled={editing}
          onChange={(e) => setForm({ ...form, id: e.target.value })}
        />
        <input
          placeholder="Название"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <input
          placeholder="rtsp://…"
          value={form.source ?? ""}
          onChange={(e) => setForm({ ...form, source: e.target.value })}
        />
        <input
          placeholder="Участок"
          value={form.area ?? ""}
          onChange={(e) => setForm({ ...form, area: e.target.value })}
        />
        <button type="submit">Сохранить</button>
        {editing && (
          <button
            type="button"
            onClick={() => {
              setForm(EMPTY);
              setEditing(false);
              setError("");
            }}
          >
            Отмена
          </button>
        )}
      </form>
    </section>
  );
}
