import { useEffect, useRef, useState } from "react";
import MjpegImage from "../components/MjpegImage";
import { getZones, listEvents, putZones } from "../../infrastructure/api";
import { MIN_EVENTS, buildHeatGrid, suggestZones, type HeatGrid } from "../../domain/heatmap";
import HeatCells from "../components/HeatCells";
import {
  ZONE_LABELS,
  type Camera,
  type Point,
  type Zone,
  type ZoneKind,
  type ZoneSchedule,
} from "../../domain/types";
import { ZONE_KINDS } from "../../domain/zoneKinds";
import {
  ZONE_COLORS,
  centroid,
  defaultZoneName,
  finishZone,
  isNearFirst,
  pointsAttr,
  removeZone,
  scheduleLabel,
  setZoneKind,
  setZoneSchedule,
  toNormalized,
  validateSchedule,
  zoneFill,
  zonesChanged,
} from "../../domain/zones";

interface Props {
  cameras: Camera[];
  readOnly?: boolean;
  /** камера, с которой открыть редактор (из экрана «Участки») */
  initialCameraId?: string;
}

const KINDS = Object.keys(ZONE_LABELS) as ZoneKind[];
const DEFAULT_SCHEDULE: ZoneSchedule = { start: "08:00", end: "12:00" };

/** Не перехватываем клавиши, пока человек печатает в поле. */
const isTyping = (target: EventTarget | null) =>
  target instanceof HTMLElement && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName);

export default function ZoneEditor({ cameras, readOnly = false, initialCameraId = "" }: Props) {
  const [cameraId, setCameraId] = useState(initialCameraId);
  const [zones, setZones] = useState<Zone[]>([]);
  const [saved, setSaved] = useState<Zone[]>([]);
  const [draft, setDraft] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null); // куда тянется следующая точка
  const [hover, setHover] = useState<string | null>(null); // зона, на которую навели в списке
  const [kind, setKind] = useState<ZoneKind>("danger");
  const [name, setName] = useState("");
  const [timed, setTimed] = useState(false); // зона действует только в свои часы
  const [schedule, setSchedule] = useState<ZoneSchedule>(DEFAULT_SCHEDULE);
  const [message, setMessage] = useState("");
  const [heat, setHeat] = useState<HeatGrid | null>(null); // тепловая карта нарушений камеры
  const screen = useRef<HTMLDivElement>(null);
  const nameField = useRef<HTMLInputElement>(null);
  const dirty = zonesChanged(zones, saved);

  useEffect(() => {
    if (!cameraId && cameras.length) setCameraId(cameras[0].id);
  }, [cameras, cameraId]);

  useEffect(() => {
    if (!cameraId) return;
    setDraft([]);
    setCursor(null);
    getZones(cameraId)
      .then((loaded) => {
        setZones(loaded);
        setSaved(loaded);
        setMessage("");
      })
      .catch((e: Error) => setMessage(e.message));
  }, [cameraId]);

  /** Нарушения камеры из журнала → тепловая карта; null — не показывать. */
  async function loadHeat(): Promise<HeatGrid | null> {
    const events = (await listEvents(1000)).filter((e) => e.camera_id === cameraId);
    const grid = buildHeatGrid(events);
    setHeat(grid);
    return grid;
  }

  async function toggleHeat() {
    if (heat) return setHeat(null);
    try {
      const grid = await loadHeat();
      if (grid && grid.count < MIN_EVENTS) setMessage(`Для тепловой карты мало нарушений: ${grid.count} из ${MIN_EVENTS}`);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  /** Авторазметка: горячие пятна карты → новые опасные зоны (в список, до «Сохранить»). */
  async function autoMark() {
    try {
      const grid = heat ?? (await loadHeat());
      const found = grid ? suggestZones(grid) : [];
      if (!found.length) return setMessage(`Мало данных для авторазметки: нужно хотя бы ${MIN_EVENTS} нарушений на камере`);
      setZones((list) =>
        found.reduce(
          (acc, item, i) => finishZone(acc, item.polygon, "danger", `Авто ${i + 1} · ${Math.round(item.share * 100)}% нарушений`),
          list,
        ),
      );
      setMessage(`Предложено зон: ${found.length}. Проверьте на кадре и нажмите «Сохранить»`);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  function close() {
    if (draft.length < 3) return;
    if (timed) {
      const problem = validateSchedule(schedule);
      if (problem) return setMessage(problem);
    }
    setMessage("");
    setZones((list) => {
      const label = name.trim() || defaultZoneName(list, kind);
      return finishZone(list, draft, kind, label, timed ? schedule : undefined);
    });
    setDraft([]);
    setCursor(null);
    setName("");
  }

  const undoPoint = () => setDraft((points) => points.slice(0, -1));
  const resetDraft = () => {
    setDraft([]);
    setCursor(null);
  };

  // клавиши: Enter — замкнуть, Backspace — убрать точку, Esc — сбросить
  const keys = useRef({ close, undoPoint, resetDraft });
  keys.current = { close, undoPoint, resetDraft };
  useEffect(() => {
    if (readOnly) return;
    const onKey = (e: KeyboardEvent) => {
      // в поле названия Enter замыкает зону (набрал имя — Enter), остальные клавиши — тексту
      if (isTyping(e.target) && !(e.key === "Enter" && e.target === nameField.current)) return;
      if (e.key === "Enter") keys.current.close();
      else if (e.key === "Backspace" || e.key === "Delete") {
        e.preventDefault();
        keys.current.undoPoint();
      } else if (e.key === "Escape") keys.current.resetDraft();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [readOnly]);

  function click(e: React.MouseEvent) {
    const rect = screen.current?.getBoundingClientRect();
    if (!rect) return;
    // клик по кадру уводит фокус из полей: Backspace и Esc снова работают с точками
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    const point = toNormalized(e.clientX, e.clientY, rect);
    if (isNearFirst(draft, point, rect)) return close(); // клик по первой точке замыкает зону
    setDraft((points) => [...points, point]);
  }

  function move(e: React.MouseEvent) {
    const rect = screen.current?.getBoundingClientRect();
    if (rect && draft.length) setCursor(toNormalized(e.clientX, e.clientY, rect));
  }

  function changeCamera(next: string) {
    if (dirty && !window.confirm("Есть несохранённые изменения зон. Перейти к другой камере без сохранения?")) return;
    setCameraId(next);
  }

  async function save() {
    const broken = zones.find((zone) => zone.schedule && validateSchedule(zone.schedule));
    if (broken?.schedule) return setMessage(`${broken.name || broken.id}: ${validateSchedule(broken.schedule)}`);
    try {
      const result = await putZones(cameraId, zones);
      setZones(result);
      setSaved(result);
      setMessage("Сохранено. Воркер подхватит зоны в течение нескольких секунд.");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  const ready = draft.length >= 3;
  return (
    <section className="zone-editor">
      <div className="zone-head">
        <h2>{readOnly ? "Зоны" : "Редактор зон"}</h2>
        <label className="field">
          <span>Камера</span>
          <select aria-label="Камера" value={cameraId} onChange={(e) => changeCamera(e.target.value)}>
            {cameras.map((camera) => (
              <option key={camera.id} value={camera.id}>
                {camera.name}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className={heat ? "active" : ""} onClick={toggleHeat}>
          Тепловая карта
        </button>
        {!readOnly && (
          <button type="button" onClick={autoMark} title="Предложить опасные зоны по местам, где чаще всего нарушают">
            Авторазметка опасных зон
          </button>
        )}
        {!readOnly && (
          <span className={dirty ? "chip warn" : "chip ok"} role="status">
            {dirty ? "Не сохранено" : "Сохранено"}
          </span>
        )}
      </div>

      {!readOnly && (
        <div className="zone-bar">
          <div className="zone-form">
            <label className="field">
              <span>Вид зоны</span>
              <select aria-label="Вид зоны" value={kind} onChange={(e) => setKind(e.target.value as ZoneKind)}>
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {ZONE_LABELS[k]}
                  </option>
                ))}
              </select>
            </label>
            <label className="field grow">
              <span>Название</span>
              <input
                ref={nameField}
                placeholder={`Название (если пусто: ${defaultZoneName(zones, kind)})`}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <div className="field">
              <label className="check">
                <input type="checkbox" checked={timed} onChange={(e) => setTimed(e.target.checked)} /> по расписанию
              </label>
              {timed && (
                <span className="zone-schedule">
                  <input
                    type="time"
                    aria-label="Зона действует с"
                    value={schedule.start}
                    onChange={(e) => setSchedule({ ...schedule, start: e.target.value })}
                  />
                  –
                  <input
                    type="time"
                    aria-label="Зона действует до"
                    value={schedule.end}
                    onChange={(e) => setSchedule({ ...schedule, end: e.target.value })}
                  />
                </span>
              )}
            </div>
          </div>
          <div className="actions zone-actions">
            <button onClick={close} disabled={!ready}>
              Замкнуть
            </button>
            <button onClick={undoPoint} disabled={!draft.length}>
              Отменить точку
            </button>
            <button onClick={resetDraft} disabled={!draft.length}>
              Сбросить
            </button>
            <button className="primary" onClick={save} disabled={!cameraId || !dirty}>
              Сохранить
            </button>
          </div>
        </div>
      )}
      {message && (
        <p className="muted zone-msg" role="status">
          {message}
        </p>
      )}

      <div className="zone-body">
      <div
        className="screen zone-stage"
        ref={screen}
        onClick={readOnly ? undefined : click}
        onMouseMove={readOnly ? undefined : move}
        onMouseLeave={() => setCursor(null)}
        style={{ cursor: readOnly ? "default" : "crosshair" }}
      >
        {cameraId && (
          <MjpegImage
            src={`/api/cameras/${encodeURIComponent(cameraId)}/mjpeg`}
            alt=""
            draggable={false}
            onError={(e) => {
              e.currentTarget.hidden = true;
            }}
          />
        )}
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="zone-svg">
          {heat && <HeatCells grid={heat} />}
          {zones.map((zone) => (
            <polygon
              key={zone.id}
              points={pointsAttr(zone.polygon)}
              fill={zoneFill(zone.kind)}
              stroke={ZONE_COLORS[zone.kind]}
              strokeWidth={hover === zone.id ? 4 : 2}
              vectorEffect="non-scaling-stroke"
              opacity={hover && hover !== zone.id ? 0.4 : 1}
            />
          ))}
          {draft.length > 0 && (
            <>
              {ready && <polygon points={pointsAttr(draft)} fill={zoneFill(kind)} stroke="none" />}
              <polyline
                points={pointsAttr(cursor ? [...draft, cursor] : draft)}
                fill="none"
                stroke="#fff"
                strokeWidth={2}
                strokeDasharray="6 4"
                vectorEffect="non-scaling-stroke"
              />
            </>
          )}
        </svg>
        {zones.map((zone) => {
          const [x, y] = centroid(zone.polygon);
          return (
            <span
              key={zone.id}
              className="zone-tag"
              style={{ left: `${x * 100}%`, top: `${y * 100}%`, borderColor: ZONE_COLORS[zone.kind] }}
            >
              {zone.name || zone.id}
              {zone.schedule ? ` · ${scheduleLabel(zone.schedule)}` : ""}
            </span>
          );
        })}
        {draft.map(([x, y], i) => (
          <span
            key={i}
            className={i === 0 ? (ready ? "zone-dot first ready" : "zone-dot first") : "zone-dot"}
            style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
          />
        ))}
      </div>

      {!readOnly && (
        <aside className="zone-aside">
          <p className="muted zone-hint">{ZONE_KINDS[kind].hint}</p>
          <p className="muted zone-steps">
            Кликайте по кадру, чтобы ставить точки. Замкнуть зону: клик по первой точке или Enter. Убрать точку:
            Backspace. Сбросить: Esc.
          </p>
        </aside>
      )}
      </div>

      <ul className="zone-list">
        {zones.length === 0 && <li className="muted">Зон на этой камере пока нет.</li>}
        {zones.map((zone) => (
          <li key={zone.id} onMouseEnter={() => setHover(zone.id)} onMouseLeave={() => setHover(null)}>
            <span className="zone-chip" style={{ background: ZONE_COLORS[zone.kind] }} aria-hidden="true" />
            <b className="zone-name">{zone.name || zone.id}</b>
            {readOnly ? (
              <span className="muted">
                {ZONE_LABELS[zone.kind]} · {scheduleLabel(zone.schedule)}
              </span>
            ) : (
              <>
                <select
                  aria-label={`Вид зоны ${zone.name || zone.id}`}
                  value={zone.kind}
                  onChange={(e) => setZones((list) => setZoneKind(list, zone.id, e.target.value as ZoneKind))}
                >
                  {KINDS.map((k) => (
                    <option key={k} value={k}>
                      {ZONE_LABELS[k]}
                    </option>
                  ))}
                </select>
                <span className="zone-schedule">
                  <label className="check">
                    <input
                      type="checkbox"
                      aria-label={`Расписание ${zone.name || zone.id}`}
                      checked={!!zone.schedule}
                      onChange={(e) =>
                        setZones((list) =>
                          setZoneSchedule(list, zone.id, e.target.checked ? DEFAULT_SCHEDULE : undefined),
                        )
                      }
                    />{" "}
                    по расписанию
                  </label>
                  {zone.schedule && (
                    <>
                      <input
                        type="time"
                        aria-label={`${zone.name || zone.id}: действует с`}
                        value={zone.schedule.start}
                        onChange={(e) =>
                          setZones((list) => setZoneSchedule(list, zone.id, { ...zone.schedule!, start: e.target.value }))
                        }
                      />
                      –
                      <input
                        type="time"
                        aria-label={`${zone.name || zone.id}: действует до`}
                        value={zone.schedule.end}
                        onChange={(e) =>
                          setZones((list) => setZoneSchedule(list, zone.id, { ...zone.schedule!, end: e.target.value }))
                        }
                      />
                    </>
                  )}
                </span>
                <button onClick={() => setZones((list) => removeZone(list, zone.id))}>Удалить</button>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
