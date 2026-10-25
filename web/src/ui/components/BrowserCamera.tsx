import { useCallback, useEffect, useRef, useState } from "react";
import {
  BROWSER_CAM_FPS,
  BROWSER_CAM_QUALITY,
  cameraErrorMessage,
  deviceLabel,
  frameSize,
  frameStatusMessage,
  frameUrl,
  statusText,
  videoConstraints,
} from "../../domain/browserCamera";

/** Камера этого устройства через браузер: getUserMedia → JPEG → POST /api/phone/{id}/frame. */
export default function BrowserCamera({
  cameraId,
  cameraName,
  autoStart = false,
  onSent,
}: {
  cameraId: string;
  cameraName?: string;
  autoStart?: boolean;
  /** сколько кадров принял сервер (растёт по ходу работы) */
  onSent?: (count: number) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const startedAt = useRef(0);
  const counters = useRef({ sent: 0, failed: 0 });
  const latest = useRef({ cameraId, cameraName, onSent });
  latest.current = { cameraId, cameraName, onSent };
  const [running, setRunning] = useState(false);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [error, setError] = useState("");
  const [stats, setStats] = useState({ sent: 0, failed: 0, elapsed: 0 });

  const stop = useCallback(() => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setRunning(false);
  }, []);

  useEffect(() => stop, [stop]);

  const refreshDevices = useCallback(async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === "videoinput"));
    } catch {
      setDevices([]);
    }
  }, []);

  const start = useCallback(
    async (wanted: string) => {
      stop();
      setError("");
      const secure = window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;
      if (!secure) return setError(cameraErrorMessage(null, false));
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia(videoConstraints(wanted));
      } catch (err) {
        return setError(cameraErrorMessage(err));
      }
      streamRef.current = stream;
      const video = videoRef.current;
      if (video) {
        video.srcObject = stream;
        void video.play().catch(() => undefined);
      }
      // названия устройств доступны только после разрешения
      void refreshDevices();
      const current = stream.getVideoTracks()[0]?.getSettings().deviceId;
      if (current) setDeviceId(current);

      const canvas = document.createElement("canvas");
      counters.current = { sent: 0, failed: 0 };
      startedAt.current = Date.now();
      setStats({ sent: 0, failed: 0, elapsed: 0 });
      let busy = false;
      timerRef.current = window.setInterval(async () => {
        const v = videoRef.current;
        const size = v ? frameSize(v.videoWidth, v.videoHeight) : null;
        if (busy || !v || !size) return;
        busy = true;
        try {
          canvas.width = size.width;
          canvas.height = size.height;
          canvas.getContext("2d")?.drawImage(v, 0, 0, size.width, size.height);
          const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/jpeg", BROWSER_CAM_QUALITY));
          if (!blob || !streamRef.current) return;
          const response = await fetch(frameUrl(latest.current.cameraId, latest.current.cameraName), {
            method: "POST",
            headers: { "Content-Type": "image/jpeg" },
            body: blob,
            credentials: "same-origin",
          });
          const problem = frameStatusMessage(response.status);
          if (problem) {
            counters.current.failed++;
            setError(problem);
          } else {
            counters.current.sent++;
            setError("");
            latest.current.onSent?.(counters.current.sent);
          }
        } catch {
          counters.current.failed++;
          setError("Нет связи с бэкендом.");
        } finally {
          busy = false;
          setStats({ ...counters.current, elapsed: (Date.now() - startedAt.current) / 1000 });
        }
      }, 1000 / BROWSER_CAM_FPS);
      setRunning(true);
    },
    [refreshDevices, stop],
  );

  useEffect(() => {
    if (autoStart) void start("");
    // только при монтировании
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="browser-cam">
      <video ref={videoRef} playsInline muted autoPlay className="browser-cam-video" hidden={!running} />
      <div className="browser-cam-row">
        {!running ? (
          <button type="button" onClick={() => void start(deviceId)}>
            Включить камеру этого устройства
          </button>
        ) : (
          <button type="button" onClick={stop}>
            Выключить
          </button>
        )}
        {devices.length > 1 && (
          <select
            aria-label="Какая камера"
            value={deviceId}
            onChange={(e) => {
              setDeviceId(e.target.value);
              if (running) void start(e.target.value);
            }}
          >
            {devices.map((device, index) => (
              <option key={device.deviceId} value={device.deviceId}>
                {deviceLabel(device, index)}
              </option>
            ))}
          </select>
        )}
      </div>
      <p className="muted" role="status">
        {statusText(running, stats.sent, stats.failed, stats.elapsed)}
      </p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
