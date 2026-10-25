import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  SCREEN_LABELS,
  allowedScreens,
  canEditZones,
  canManageAreas,
  canManageCameras,
  canManageUsers,
  canMark,
  isViewOnly,
  pathForScreen,
  type Access,
  type Screen,
} from "../domain/auth";
import { queueOf } from "../domain/queue";
import { Bump, FadeImg, Lightbox, useLingering } from "../ui/motion";
import { BellIcon, ShieldCheckIcon } from "../ui/components/icons";
import AnalyticsView from "../ui/views/AnalyticsView";
import { canControlLinq } from "../domain/linq";
import { listCameras } from "../infrastructure/api";
import DemoVideosView from "../ui/views/DemoVideosView";
import LinqView from "../ui/views/LinqView";
import AreasView from "../ui/views/AreasView";
import CamerasView from "../ui/views/CamerasView";
import JournalView from "../ui/views/JournalView";
import MasterView from "../ui/views/MasterView";
import MonitorView from "../ui/views/MonitorView";
import QueueView from "../ui/views/QueueView";
import UsersView from "../ui/views/UsersView";
import ZoneEditor from "../ui/views/ZoneEditor";
import { useAlerts } from "./useAlerts";
import { useEventFeed } from "./useEventFeed";

interface Props {
  role: Access;
  start: Screen;
  userSlot?: ReactNode;
  meLogin?: string;
  areas?: string[];
}

/** Оболочка: шапка, вкладки по роли и выбранный экран. */
export default function App({ role, start, userSlot, meLogin = "", areas }: Props) {
  const feed = useEventFeed();
  const [alertsOn, toggleAlerts] = useAlerts();
  const [tab, setTab] = useState<Screen>(start);
  const [zoom, setZoom] = useState<string | null>(null);
  const zoomShown = useLingering(zoom);
  const [zoneCamera, setZoneCamera] = useState(""); // камера, выбранная на экране «Участки» для разметки зон
  const tabsRef = useRef<HTMLElement>(null);
  const screens = allowedScreens(role);
  const waiting = queueOf(feed.events).length;

  function open(screen: Screen) {
    setTab(screen);
    window.history.pushState(null, "", pathForScreen(screen));
  }

  // на телефоне вкладки прокручиваются: активную держим в видимой части
  useEffect(() => {
    tabsRef.current
      ?.querySelector<HTMLElement>("[aria-current=page]")
      ?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [tab]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <ShieldCheckIcon />
          </span>
          <h1>Zorky</h1>
        </div>
        <nav className="tabs" aria-label="Разделы" ref={tabsRef}>
          {screens.map((screen) => (
            <button
              key={screen}
              className={tab === screen ? "active" : ""}
              aria-current={tab === screen ? "page" : undefined}
              onClick={() => open(screen)}
            >
              {SCREEN_LABELS[screen]}
              {screen === "queue" && waiting > 0 && <Bump value={waiting} className="tab-count">{waiting}</Bump>}
            </button>
          ))}
        </nav>
        <div className="topbar-end">
          <span className={feed.online ? "badge online" : "badge offline"}>
            {feed.online ? "Лента онлайн" : "Нет связи с бэкендом"}
          </span>
          {isViewOnly(role, tab) && <span className="badge view-only">только просмотр</span>}
          <button
            className="icon-btn"
            aria-pressed={alertsOn}
            aria-label={alertsOn ? "Звук и вибрация включены" : "Звук и вибрация выключены"}
            title={alertsOn ? "Звук и вибрация включены — нажмите, чтобы выключить" : "Звук и вибрация выключены"}
            onClick={toggleAlerts}
          >
            <BellIcon off={!alertsOn} />
          </button>
          {userSlot}
        </div>
      </header>
      {feed.error && <p className="error">{feed.error}</p>}
      {feed.notice && (
        <div className="toast" role="alert" onClick={feed.clearNotice}>
          {feed.notice}
        </div>
      )}
      {feed.actionError && (
        <div className="toast" role="alert" onClick={feed.clearActionError}>
          {feed.actionError.text}
        </div>
      )}

      <main key={tab} className="fx-screen">
        {tab === "monitor" && (
          <MonitorView
            events={feed.events}
            cameras={feed.cameras}
            role={role}
            now={feed.now}
            seen={feed.seen}
            actionError={feed.actionError}
            onMark={feed.mark}
            onZoom={setZoom}
          />
        )}
        {tab === "journal" && <JournalView cameras={feed.cameras} />}
        {tab === "master" && <MasterView areas={areas} />}
        {tab === "queue" && canMark(role) && (
          <QueueView
            events={feed.events}
            cameras={feed.cameras}
            now={feed.now}
            onConfirm={(id) => feed.mark(id, "confirmed")}
            onFalse={(id, reason) => feed.mark(id, "false", reason)}
            onHandoff={feed.confirmAndHand}
            actionError={feed.actionError}
          />
        )}
        {tab === "cameras" && canManageCameras(role) && (
          <CamerasView cameras={feed.cameras} onChange={feed.setCameras} />
        )}
        {tab === "users" && canManageUsers(role) && <UsersView meLogin={meLogin} />}
        {tab === "analytics" && <AnalyticsView cameras={feed.cameras} />}
        {tab === "demo" && screens.includes("demo") && (
          <DemoVideosView cameras={feed.cameras} onCamerasChange={() => void listCameras().then(feed.setCameras)} />
        )}
        {tab === "linq" && screens.includes("linq") && <LinqView readOnly={!canControlLinq(role)} />}
        {tab === "areas" && canManageAreas(role) && (
          <AreasView
            cameras={feed.cameras}
            onCamerasChange={feed.setCameras}
            onOpenZones={(cameraId) => {
              setZoneCamera(cameraId);
              open("zones");
            }}
          />
        )}
        {tab === "zones" && screens.includes("zones") && (
          <ZoneEditor cameras={feed.cameras} readOnly={!canEditZones(role)} initialCameraId={zoneCamera} />
        )}
      </main>

      <Lightbox open={zoom !== null} onClose={() => setZoom(null)} label="Снимок нарушения">
        {zoomShown && <FadeImg src={zoomShown} alt="снимок нарушения" loading="eager" />}
      </Lightbox>
    </div>
  );
}
