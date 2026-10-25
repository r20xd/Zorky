// Оператор → мастер → журнал на живом демо: передача, «Принял», живой поток, очередь и горячие клавиши.
import { launch, login, reporter } from "./lib.mjs";

const { check, finish } = reporter();
const browser = await launch();
const op = await login(browser, "operator");
const ms = await login(browser, "master");

check("мастер после входа видит экран мастера", (await ms.page.locator("nav.tabs button.active").innerText()).trim() === "Мастер");
check("у мастера нет вкладок оператора", (await ms.page.locator("nav.tabs button").allTextContents()).join("|") === "Мастер|Журнал");

// первое событие приходит через ~30 с после старта демо; ждём такое, с которым есть что делать:
// ещё не переданное мастеру (кнопка) или уже ушедшее к нему само (критичное)
await op.page.waitForSelector(
  '.feed li.event:has(button:has-text("Передать мастеру")), .feed li.event:has(.status-sent_to_master)',
  { timeout: 180000 },
);
check("в ленте оператора появилось событие", true);

// передача мастеру (критичные уходят сами, остальные — кнопкой)
const handButton = op.page.getByRole("button", { name: "Передать мастеру" }).first();
if (await handButton.count()) {
  const card = op.page.locator(".feed li.event", { has: handButton }).first();
  const title = (await card.locator(".event-title").innerText()).trim();
  await handButton.click();
  await ms.page.waitForSelector(".m-card", { timeout: 8000 }).catch(() => {});
  // у мастера могут быть и другие карточки (критичные приходят сами): ищем среди всех
  const masterTitles = (await ms.page.locator(".m-card .m-title").allInnerTexts().catch(() => [])).map((t) => t.trim());
  check("карточка дошла до мастера без перезагрузки", masterTitles.length > 0, masterTitles.join(", "));
  check("заголовок у мастера совпадает с лентой оператора", masterTitles.includes(title), `${title} / ${masterTitles.join(", ")}`);
} else {
  await ms.page.waitForSelector(".m-card", { timeout: 15000 }).catch(() => {});
  check("критичное событие ушло мастеру само", (await ms.page.locator(".m-card").count()) >= 1);
}
await op.page.waitForSelector(".feed li.event .status-sent_to_master", { timeout: 6000 }).catch(() => {});
check("оператор видит статус «Передано мастеру» без перезагрузки", (await op.page.locator(".feed li.event .status-sent_to_master").count()) >= 1);

// живой поток на карточке мастера
await ms.page.getByRole("button", { name: "Смотреть камеру вживую" }).first().click();
await ms.page.waitForTimeout(2500);
const live = await ms.page.evaluate(() => {
  const img = document.querySelector('.m-card img[alt="Камера вживую"]');
  return img ? { src: img.getAttribute("src"), loaded: img.naturalWidth > 0 } : null;
});
check("«Смотреть камеру вживую» показывает поток камеры", !!live && live.src.includes("/mjpeg") && live.loaded, live ? live.src : "нет");
await ms.page.getByRole("button", { name: "Показать кадр нарушения" }).first().click();

// мастер принимает → оператор видит «Решено» сам
await ms.page.getByRole("button", { name: /Принял, иду/ }).first().click();
await op.page.waitForSelector(".feed li.event .status-resolved", { timeout: 6000 }).catch(() => {});
check("оператор видит «Решено» после «Принял» у мастера", (await op.page.locator(".feed li.event .status-resolved").count()) >= 1);

// журнал
await op.page.locator("nav.tabs button", { hasText: "Журнал" }).click();
await op.page.waitForSelector(".journal table tbody tr", { timeout: 8000 });
const rows = await op.page.locator(".journal table tbody tr").allInnerTexts();
check("в журнале есть решённое событие с временем реакции", rows.some((r) => /Решено\s+\d+ с/.test(r.replace(/\n/g, " "))));

// журнал как доказательство: в выгрузке видно, кто и когда отреагировал
const csv = await op.page.evaluate(async () => (await fetch("/api/events.csv")).text());
const rowsCsv = csv.replace(/^\uFEFF/, "").trim().split(/\r?\n/);
const head = rowsCsv[0].split(",");
const evidence = rowsCsv.slice(1).map((line) => line.split(",")).find((cols) => cols[head.indexOf("closed_by")] === "master");
check(
  "выгрузка журнала: кто отреагировал первым, кто закрыл и когда, кадр и участок",
  !!evidence && !!evidence[head.indexOf("reaction_by")] && !!evidence[head.indexOf("closed_at")] && !!evidence[head.indexOf("snapshot")] && !!evidence[head.indexOf("area")],
  evidence ? evidence.slice(-8).join(" | ") : head.join("|"),
);

// очередь и горячие клавиши
await op.page.locator("nav.tabs button", { hasText: "Требуют решения" }).click();
await op.page.waitForTimeout(700);
const before = await op.page.locator(".queue li.event").count();
if (before) {
  // горячие клавиши работают с первой карточкой; следим за ней самой, а не за числом карточек
  // (пока идёт проверка, демо присылает новые нарушения)
  const firstCard = (await op.page.locator(".queue li.event").first().innerText()).trim();
  await op.page.keyboard.press("2");
  await op.page.waitForTimeout(300);
  check("клавиша 2 открывает причины ложного", (await op.page.locator(".queue .reasons button").count()) === 5);
  await op.page.keyboard.press("1");
  await op.page.waitForTimeout(900);
  const cards = await op.page.locator(".queue li.event").allInnerTexts();
  check("после выбора причины событие уходит из очереди", !cards.some((text) => text.trim() === firstCard));
}

check("нет ошибок в консоли у оператора", op.errors.length === 0, op.errors.join(" | "));
check("нет ошибок в консоли у мастера", ms.errors.length === 0, ms.errors.join(" | "));
await browser.close();
finish();
