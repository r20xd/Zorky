// Все роли × все экраны × десктоп и телефон: доступность (axe-core), ошибки консоли, переполнение по ширине.
import { createRequire } from "node:module";
import { launch, login, reporter } from "./lib.mjs";

const AXE = createRequire(import.meta.url).resolve("axe-core/axe.min.js");
const ROLES = ["operator", "master", "specialist", "manager", "admin"];
const VIEWPORTS = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } };
const { check, finish } = reporter();
const browser = await launch();
const violations = new Map();
const overflow = [];
const consoleErrors = [];

for (const role of ROLES) {
  for (const [name, viewport] of Object.entries(VIEWPORTS)) {
    const { context, page, errors } = await login(browser, role, viewport);
    const tabs = await page.locator("nav.tabs button").count();
    for (let i = 0; i < tabs; i++) {
      await page.locator("nav.tabs button").nth(i).click();
      await page.waitForTimeout(700);
      const tab = (await page.locator("nav.tabs button").nth(i).innerText()).replace(/\d+$/, "").trim();
      await page.addScriptTag({ path: AXE });
      const result = await page.evaluate(() => axe.run(document, { resultTypes: ["violations"] }));
      for (const v of result.violations) violations.set(`${v.id} (${v.impact}): ${v.help}`, `${role}/${name}/${tab}`);
      // обычное окно без эмуляции телефона: иначе браузер молча растягивает вёрстку вместо прокрутки
      const extra = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      if (extra > 0) overflow.push(`${role}/${name}/${tab}: +${extra}px`);
    }
    consoleErrors.push(...errors.map((e) => `${role}/${name}: ${e}`));
    await context.close();
  }
}
// движение: экран въезжает анимацией, а при «меньше движения» ни одна анимация и переход не длятся дольше 1 мс
const longest = (page) =>
  page.evaluate(() => {
    const ms = (v) => Math.max(...v.split(",").map((s) => (s.trim().endsWith("ms") ? parseFloat(s) : parseFloat(s) * 1000)));
    let max = 0;
    for (const el of document.querySelectorAll("*")) {
      const st = getComputedStyle(el);
      max = Math.max(max, ms(st.animationDuration) + ms(st.animationDelay), ms(st.transitionDuration) + ms(st.transitionDelay));
    }
    return max;
  });
{
  const { context, page } = await login(browser, "operator");
  const name = await page.evaluate(() => getComputedStyle(document.querySelector("main.fx-screen")).animationName);
  check("движение: экран появляется анимацией", name !== "none", name);
  await context.close();
}
for (const role of ["operator", "manager"]) {
  const { context, page } = await login(browser, role, undefined, { reducedMotion: "reduce" });
  let worst = 0;
  const tabs = await page.locator("nav.tabs button").count();
  for (let i = 0; i < tabs; i++) {
    await page.locator("nav.tabs button").nth(i).click();
    await page.waitForTimeout(300);
    worst = Math.max(worst, await longest(page));
  }
  check(`меньше движения (${role}): анимаций дольше 1 мс нет`, worst <= 1, `${worst} мс`);
  await context.close();
}
await browser.close();
check("axe: нарушений доступности нет", violations.size === 0, [...violations].map(([k, w]) => `${k} @ ${w}`).join("; "));
check("страницы не шире экрана (320–1440)", overflow.length === 0, overflow.join("; "));
check("нет ошибок в консоли", consoleErrors.length === 0, consoleErrors.join(" | "));
finish();
