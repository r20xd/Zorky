// Скриншоты всех экранов на 360/768/1280 в web/shots/ (npm run shots). Падает при горизонтальной прокрутке.
// API подменён заглушками — бэкенд не нужен. Chromium: PLAYWRIGHT_BROWSERS_PATH, Edge или /opt/pw-browsers/chromium.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const PORT = 5199;
const BASE = `http://127.0.0.1:${PORT}`;
const WIDTHS = [360, 768, 1280];
const SCREENS = [
  ["monitor", "/", "Мониторинг"],
  ["journal", "/", "Журнал"],
  ["analytics", "/", "Аналитика"],
  ["zones", "/", "Зоны"],
  ["master", "/master", null],
  ["users", "/", "Пользователи"],
];

const now = Date.now();
const types = ["no_helmet", "danger_zone", "smoking", "fall", "phone", "running"];
const events = Array.from({ length: 12 }, (_, i) => {
  const ts = new Date(now - i * 60000).toISOString();
  return {
    id: `e${i}`, camera_id: `cam-${(i % 3) + 1}`, ts, frame_id: i, type: types[i % types.length],
    bbox: [0.2, 0.2, 0.4, 0.8], confidence: 0.9, track_id: i, zone_id: "z-1",
    severity: i % 4 === 0 ? "critical" : "high", incident_id: `inc-${i}`, count: 1,
    first_seen: ts, last_seen: ts, status: i % 3 === 0 ? "sent_to_master" : "new",
    status_history: [], received_at: ts,
  };
});
const cameras = [1, 2, 3].map((n) => ({ id: `cam-${n}`, name: `Камера ${n}` }));
const zones = [{ id: "z-1", kind: "danger", name: "Зона 1", polygon: [[0.1, 0.1], [0.6, 0.1], [0.6, 0.7]] }];

const json = (body, headers = {}) => ({ status: 200, contentType: "application/json", headers, body: JSON.stringify(body) });

async function mock(page) {
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    if (p === "/api/auth/me") return route.fulfill({ status: 404, body: "" });
    if (p === "/api/stream") return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": ok\n\n" });
    if (p === "/api/cameras") return route.fulfill(json(cameras));
    if (p.endsWith("/zones")) return route.fulfill(json(zones));
    if (p.endsWith("/mjpeg")) return route.fulfill({ status: 404, body: "" });
    if (p === "/api/users") return route.fulfill(json([{ login: "ivanov", name: "Иванов", role: "operator", active: true, created_by: "admin", created_at: new Date().toISOString() }]));
    if (p.endsWith("/reset-password")) return route.fulfill(json({ temp_password: "k7Xp-92Qm-Zt4w" }));
    if (p === "/api/metrics") return route.fulfill(json([]));
    if (p === "/api/events") {
      const st = url.searchParams.get("status");
      const list = st ? events.filter((e) => e.status === st) : events;
      return route.fulfill(json(list, { "X-Total-Count": String(list.length) }));
    }
    return route.fulfill(json([]));
  });
}

const server = spawn("npx", ["vite", "--port", String(PORT), "--strictPort", "--host", "127.0.0.1"], { stdio: "ignore", shell: process.platform === "win32" });
const stop = () => server.kill();
process.on("exit", stop);

async function waitServer() {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(BASE)).ok) return; } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("vite не поднялся");
}

const fallback = "/opt/pw-browsers/chromium";
let browser;
try {
  await waitServer();
  try {
    browser = await chromium.launch();
  } catch {
    // без скачанного Chromium: Edge на Windows, затем путь из контейнера
    browser = await chromium.launch({ channel: "msedge" }).catch(() => chromium.launch({ executablePath: fallback }));
  }
  mkdirSync("shots", { recursive: true });
  const bad = [];
  for (const width of WIDTHS) {
    const context = await browser.newContext({ viewport: { width, height: width < 700 ? 780 : 900 } });
    const page = await context.newPage();
    await mock(page);
    for (const [name, path, tab] of SCREENS) {
      await page.goto(BASE + path);
      if (tab) await page.getByRole("button", { name: tab, exact: true }).click();
      if (name === "users") {
        page.once("dialog", (d) => d.accept());
        await page.getByRole("button", { name: "Сбросить пароль" }).click();
        await page.locator(".temp-password").waitFor();
      }
      await page.waitForTimeout(600);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      await page.screenshot({ path: `shots/${name}-${width}.png`, fullPage: true });
      if (overflow > 0) bad.push(`${name} @${width}: горизонтальная прокрутка +${overflow}px`);
    }
    await context.close();
  }
  if (bad.length) {
    console.error(bad.join("\n"));
    process.exitCode = 1;
  } else {
    console.log(`ok: ${SCREENS.length * WIDTHS.length} снимков в web/shots/`);
  }
} finally {
  await browser?.close();
  stop();
}
