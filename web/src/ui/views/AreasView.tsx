import { useCallback, useEffect, useState } from "react";
import {
  deleteArea,
  getZones,
  listAreas,
  listCameras,
  patchCamera,
  putArea,
  saveCamera,
} from "../../infrastructure/api";
import {
  SOURCE_LABELS,
  areaRules,
  cleanSchedule,
  effectiveRules,
  nextCameraId,
  rulesSummary,
  sameSchedule,
  scheduleProblem,
  validateAreaName,
} from "../../domain/areaRules";
import { DEFAULT_RULES } from "../../domain/ruleCatalog";
import type { Area, Camera, RuleSchedule, ViolationType, Zone } from "../../domain/types";
import { BROWSER_CAM_NAME, nextBrowserCameraId } from "../../domain/browserCamera";
import BrowserCamera from "../components/BrowserCamera";
import RulesChecklist from "../components/RulesChecklist";

interface Props {
  cameras: Camera[];
  onCamerasChange: (list: Camera[]) => void;
  /** открыть разметку зон нужной камеры */
  onOpenZones: (cameraId: string) => void;
}

/** Участки: специалист по охране труда вводит участок, выбирает контролируемые нарушения и камеры. */
export default function AreasView({ cameras, onCamerasChange, onOpenZones }: Props) {
  const [areas, setAreas] = useState<Area[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    const [nextAreas, nextCameras] = await Promise.all([listAreas(), listCameras()]);
    setAreas(nextAreas);
    onCamerasChange(nextCameras);
  }, [onCamerasChange]);

  useEffect(() => {
    reload().catch((e: Error) => setError(e.message));
  }, [reload]);

  async function guarded(action: () => Promise<unknown>, done = "") {
    try {
      await action();
      await reload();
      setError("");
      setMessage(done);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="areas">
      <div className="areas-head">
        <h2>Участки</h2>
        {!creating && <button onClick={() => setCreating(true)}>Новый участок</button>}
      </div>
      <p className="muted">
        Для каждого участка выберите, какие нарушения контролировать, и разметьте зоны на кадре. У камеры могут быть
        свои правила: они важнее правил участка.
      </p>
      {error && <p className="error">{error}</p>}
      {message && (
        <p className="notice-ok" role="status">
          {message}
        </p>
      )}
      {creating && (
        <NewAreaForm
          areas={areas}
          cameras={cameras}
          onCancel={() => setCreating(false)}
          onCreated={(name, firstCamera) => {
            setCreating(false);
            setOpen(name);
            setMessage(
              firstCamera
                ? `Участок «${name}» создан. Осталось разметить зоны на кадре.`
                : `Участок «${name}» создан. Добавьте камеры.`,
            );
            void reload();
          }}
          onOpenZones={onOpenZones}
        />
      )}
      {areas.length === 0 && !creating && <p className="muted">Участков пока нет.</p>}
      <ul className="area-list">
        {areas.map((area) => {
          const members = cameras.filter((camera) => camera.area === area.name);
          const expanded = open === area.name;
          return (
            <li key={area.name} className="area-card">
              <button
                className="area-title"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : area.name)}
              >
                <b>{area.name}</b>
                <span className="muted">
                  {members.length} кам. · контролируется {rulesSummary(areaRules(area))}
                  {area.rules ? "" : " (по умолчанию)"}
                </span>
              </button>
              {expanded && (
                <AreaPanel
                  key={`${area.name}:${area.rules?.join() ?? ""}`}
                  area={area}
                  members={members}
                  free={cameras.filter((camera) => !camera.area)}
                  onSaveRules={(rules, schedule) =>
                    guarded(() => putArea(area.name, rules, schedule), "Правила участка сохранены")
                  }
                  onMove={(id, to) => guarded(() => patchCamera(id, { area: to }))}
                  onCameraRules={(id, rules, schedule) =>
                    guarded(
                      () => patchCamera(id, { rules, rule_schedule: schedule }),
                      "Правила камеры сохранены",
                    )
                  }
                  onDelete={() =>
                    guarded(async () => {
                      await deleteArea(area.name);
                      setOpen(null);
                    }, `Участок «${area.name}» удалён`)
                  }
                  onOpenZones={onOpenZones}
                />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function AreaPanel({
  area,
  members,
  free,
  onSaveRules,
  onMove,
  onCameraRules,
  onDelete,
  onOpenZones,
}: {
  area: Area;
  members: Camera[];
  free: Camera[];
  onSaveRules: (rules: string[] | null, schedule: RuleSchedule | null) => void;
  onMove: (cameraId: string, area: string | null) => void;
  onCameraRules: (cameraId: string, rules: string[] | null, schedule: RuleSchedule | null) => void;
  onDelete: () => void;
  onOpenZones: (cameraId: string) => void;
}) {
  const saved = areaRules(area);
  const [draft, setDraft] = useState<ViolationType[]>(saved);
  const [windows, setWindows] = useState<RuleSchedule>(area.rule_schedule ?? {});
  const problem = scheduleProblem(windows);
  const dirty = draft.join() !== saved.join() || !sameSchedule(area.rule_schedule, cleanSchedule(draft, windows) ?? {});
  const [editingCamera, setEditingCamera] = useState<string | null>(null);

  return (
    <div className="area-panel">
      <h3>Что контролировать на участке</h3>
      <RulesChecklist
        name={`area-${area.name}`}
        value={draft}
        onChange={setDraft}
        schedule={windows}
        onSchedule={setWindows}
      />
      {problem && <p className="error">{problem}</p>}
      <div className="actions">
        <button disabled={!dirty || !!problem} onClick={() => onSaveRules(draft, cleanSchedule(draft, windows))}>
          Сохранить правила участка
        </button>
        {area.rules && <button onClick={() => onSaveRules(null, null)}>Сбросить на умолчания</button>}
      </div>
      <h3>Камеры участка</h3>
      {members.length === 0 && <p className="muted">На участке нет камер.</p>}
      <ul className="area-cameras">
        {members.map((camera) => {
          const state = effectiveRules(camera, area);
          return (
            <li key={camera.id}>
              <div className="area-camera-row">
                <b>{camera.name}</b> <span className="muted">{camera.id}</span>
                <span className="muted">
                  {" "}
                  · {rulesSummary(state.enabled)}, {SOURCE_LABELS[state.source]}
                </span>
                <button onClick={() => setEditingCamera(editingCamera === camera.id ? null : camera.id)}>
                  Правила камеры
                </button>
                <button onClick={() => onOpenZones(camera.id)}>Зоны</button>
                <button onClick={() => onMove(camera.id, null)}>Убрать с участка</button>
              </div>
              {editingCamera === camera.id && <CameraRules camera={camera} area={area} onSave={onCameraRules} />}
            </li>
          );
        })}
      </ul>
      {free.length > 0 && (
        <label className="add-camera">
          Добавить существующую камеру{" "}
          <select
            aria-label="Добавить камеру на участок"
            value=""
            onChange={(e) => e.target.value && onMove(e.target.value, area.name)}
          >
            <option value="">выберите…</option>
            {free.map((camera) => (
              <option key={camera.id} value={camera.id}>
                {camera.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {members.length === 0 && (
        <div className="actions">
          <button
            onClick={() => {
              if (window.confirm(`Удалить участок «${area.name}»?`)) onDelete();
            }}
          >
            Удалить участок
          </button>
        </div>
      )}
    </div>
  );
}

/** Свои правила камеры поверх правил участка; зоны камеры подгружаются для подсказки. */
function CameraRules({
  camera,
  area,
  onSave,
}: {
  camera: Camera;
  area: Area;
  onSave: (cameraId: string, rules: string[] | null, schedule: RuleSchedule | null) => void;
}) {
  const [draft, setDraft] = useState<ViolationType[]>(effectiveRules(camera, area).enabled);
  const [windows, setWindows] = useState<RuleSchedule>(camera.rule_schedule ?? {});
  const problem = scheduleProblem(windows);
  const [zones, setZones] = useState<Zone[]>([]);
  useEffect(() => {
    getZones(camera.id)
      .then(setZones)
      .catch(() => setZones([]));
  }, [camera.id]);
  return (
    <div className="camera-rules">
      <RulesChecklist
        name={`cam-${camera.id}`}
        value={draft}
        onChange={setDraft}
        zones={zones}
        schedule={windows}
        onSchedule={setWindows}
      />
      {problem && <p className="error">{problem}</p>}
      <div className="actions">
        <button disabled={!!problem} onClick={() => onSave(camera.id, draft, cleanSchedule(draft, windows))}>
          Сохранить правила камеры
        </button>
        {camera.rules && <button onClick={() => onSave(camera.id, null, null)}>Как на участке</button>}
      </div>
    </div>
  );
}

function NewAreaForm({
  areas,
  cameras,
  onCancel,
  onCreated,
  onOpenZones,
}: {
  areas: Area[];
  cameras: Camera[];
  onCancel: () => void;
  onCreated: (name: string, firstCameraId: string | null) => void;
  onOpenZones: (cameraId: string) => void;
}) {
  const [name, setName] = useState("");
  const [rules, setRules] = useState<ViolationType[]>(DEFAULT_RULES);
  const [picked, setPicked] = useState<string[]>([]);
  const [fresh, setFresh] = useState({ name: "", source: "" });
  const [webcam, setWebcam] = useState(false); // источник «Вебкамера»: кадры шлёт браузер
  const [webcamFrames, setWebcamFrames] = useState(0);
  const [webcamId] = useState(() => nextBrowserCameraId(cameras));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const free = cameras.filter((camera) => !camera.area);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const problem = validateAreaName(name, areas);
    if (problem) return setError(problem);
    if (webcam && webcamFrames === 0) return setError("включите камеру этого устройства и дождитесь кадров");
    if (!webcam && fresh.name.trim() && !fresh.source.trim()) {
      return setError("у новой камеры укажите адрес или вебкамеру");
    }
    setBusy(true);
    try {
      const area = name.trim();
      await putArea(area, rules);
      let first: string | null = null;
      for (const id of picked) {
        await patchCamera(id, { area });
        first ??= id;
      }
      if (webcam) {
        // камера уже заведена бэкендом первым кадром: привязываем к участку
        await patchCamera(webcamId, { area, name: fresh.name.trim() || BROWSER_CAM_NAME });
        first ??= webcamId;
      } else if (fresh.name.trim()) {
        const id = nextCameraId(cameras);
        await saveCamera({ id, name: fresh.name.trim(), source: fresh.source.trim(), area });
        first ??= id;
      }
      onCreated(area, first);
      if (first) onOpenZones(first);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="area-new" onSubmit={submit}>
      <h3>Новый участок</h3>
      {error && <p className="error">{error}</p>}
      <label>
        Название
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Например, Склад ГСМ" />
      </label>
      <h4>Камеры</h4>
      {free.length > 0 && (
        <fieldset className="rule-list">
          <legend className="muted">Свободные камеры</legend>
          {free.map((camera) => (
            <label key={camera.id} className="rule-item">
              <input
                type="checkbox"
                checked={picked.includes(camera.id)}
                onChange={() =>
                  setPicked((list) =>
                    list.includes(camera.id) ? list.filter((id) => id !== camera.id) : [...list, camera.id],
                  )
                }
              />
              <span className="rule-name">{camera.name}</span>
              <span className="rule-meta">{camera.id}</span>
            </label>
          ))}
        </fieldset>
      )}
      <div className="cam-form">
        <input
          aria-label="Название новой камеры"
          placeholder="Новая камера: название"
          value={fresh.name}
          onChange={(e) => setFresh({ ...fresh, name: e.target.value })}
        />
        {!webcam && (
          <input
            aria-label="Адрес новой камеры"
            placeholder="rtsp://… или номер вебкамеры"
            value={fresh.source}
            onChange={(e) => setFresh({ ...fresh, source: e.target.value })}
          />
        )}
        <button
          type="button"
          aria-pressed={webcam}
          onClick={() => {
            setWebcam(!webcam);
            if (!webcam) setFresh({ name: fresh.name || BROWSER_CAM_NAME, source: "" });
          }}
        >
          {webcam ? "Другой источник (адрес)" : "Вебкамера этого компьютера"}
        </button>
      </div>
      {webcam && (
        <BrowserCamera cameraId={webcamId} cameraName={fresh.name.trim() || BROWSER_CAM_NAME} onSent={setWebcamFrames} />
      )}
      <h4>Что контролировать</h4>
      <RulesChecklist name="new-area" value={rules} onChange={setRules} />
      <div className="actions">
        <button type="submit" disabled={busy}>
          Создать участок
        </button>
        <button type="button" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}
