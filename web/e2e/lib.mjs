// Общее для сквозных проверок: адрес демо, вход по роли, отчёт PASS/FAIL.
// Нужно запущенное демо со входом: `py tasks.py demo --login` (пароль роли — demo-<роль>-1).
import { chromium } from "@playwright/test";

export const BASE = process.env.BASE ?? "http://localhost:5173";

export function reporter() {
  const results = [];
  return {
    check(name, ok, detail = "") {
      results.push(ok);
      console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
    },
    finish() {
      const failed = results.filter((ok) => !ok).length;
      console.log(`\nитого: ${results.length - failed} ок, ${failed} провалено`);
      process.exit(failed ? 1 : 0);
    },
  };
}

// без скачанного Chromium — Edge (есть на любой Windows)
export const launch = () => chromium.launch().catch(() => chromium.launch({ channel: "msedge" }));

/** Входит под ролью; ошибки консоли и страницы копятся в errors (401 до входа — норма).
 * reducedMotion: "reduce" — как у человека с «меньше движения» в системе; по умолчанию — E2E_REDUCED_MOTION=1. */
export async function login(
  browser,
  role,
  viewport = { width: 1440, height: 900 },
  { reducedMotion = process.env.E2E_REDUCED_MOTION ? "reduce" : "no-preference" } = {},
) {
  const context = await browser.newContext({ viewport, reducedMotion });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
  page.on("console", (m) => {
    if (m.type() === "error" && !/status of (401|403|409)/.test(m.text())) errors.push(m.text().slice(0, 160));
  });
  page.on("dialog", (d) => d.accept());
  await page.goto(BASE + "/");
  await page.getByPlaceholder("Логин").fill(role);
  await page.getByPlaceholder("Пароль").fill(`demo-${role}-1`);
  await page.getByRole("button", { name: "Войти" }).click();
  await page.waitForSelector("nav.tabs button");
  return { context, page, errors };
}

/** fetch из страницы: идёт с cookie вошедшей роли. */
export const api = (page, path, init) =>
  page.evaluate(
    async ([p, i]) => {
      const r = await fetch(p, i);
      return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
    },
    [path, init],
  );
