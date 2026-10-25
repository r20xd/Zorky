// Админ и специалист на живом демо: камеры, пользователи, зоны, права доступа.
import { BASE, api, launch, login, reporter } from "./lib.mjs";

const { check, finish } = reporter();
const qa = `qa.${Date.now().toString(36)}`; // своя учётка на каждый прогон
const browser = await launch();

// ---------- админ: камеры ----------
const admin = await login(browser, "admin");
const A = admin.page;
await A.locator("nav.tabs button", { hasText: "Камеры" }).click();
await A.waitForSelector(".cams-table");
await A.getByPlaceholder("id").fill("cam-qa");
await A.getByPlaceholder("Название").fill("Цех QA");
await A.getByPlaceholder("Участок").fill("Участок 9");
await A.locator(".cams-form").getByRole("button", { name: "Сохранить" }).click();
await A.waitForTimeout(500);
check("без адреса RTSP форма показывает ошибку", /RTSP/.test(await A.locator("body").innerText()));
await A.getByPlaceholder("rtsp://…").fill("rtsp://user:pass@10.0.0.5:554/stream");
await A.locator(".cams-form").getByRole("button", { name: "Сохранить" }).click();
await A.waitForTimeout(800);
check("камера добавилась в таблицу", (await A.locator(".cams-table").innerText()).includes("Цех QA"));
check("пароль RTSP в таблице скрыт", !(await A.locator(".cams-table").innerText()).includes("pass"));
check("камера есть в API с участком", (await api(A, "/api/cameras")).body?.some((c) => c.id === "cam-qa" && c.area === "Участок 9"));
await A.locator(".cams-table tr", { hasText: "cam-qa" }).getByRole("button", { name: "Править" }).click();
await A.getByPlaceholder("Название").fill("Цех QA-2");
await A.locator(".cams-form").getByRole("button", { name: "Сохранить" }).click();
await A.waitForTimeout(800);
check("камера переименована", (await A.locator(".cams-table").innerText()).includes("Цех QA-2"));
await A.locator(".cams-table tr", { hasText: "cam-qa" }).getByRole("button", { name: "Удалить" }).click();
await A.waitForTimeout(800);
check("камера удалена", !(await A.locator(".cams-table").innerText()).includes("cam-qa"));

// ---------- админ: пользователи ----------
await A.locator("nav.tabs button", { hasText: "Пользователи" }).click();
await A.waitForSelector("table");
await A.getByPlaceholder("Логин").fill(qa);
await A.getByPlaceholder("Имя").fill("Мастер QA");
await A.locator("form.cam-form select").selectOption("master");
await A.getByPlaceholder("Участки через запятую").fill("Участок 9");
await A.locator("form.cam-form").getByRole("button", { name: "Добавить" }).click();
await A.waitForSelector(".temp-password", { timeout: 5000 }).catch(() => {});
const temp = await A.locator(".temp-password code").first().innerText().catch(() => "");
check("создан пользователь, показан временный пароль", temp.length >= 8);
check("пользователь в списке с участком", ((await api(A, "/api/users")).body ?? []).some((u) => u.login === qa && u.areas?.[0] === "Участок 9"));
await A.locator("table tr", { hasText: qa }).getByRole("button", { name: "Отключить" }).click();
await A.waitForTimeout(800);
check("пользователь отключён", ((await api(A, "/api/users")).body ?? []).find((u) => u.login === qa)?.active === false);
const dup = await api(A, "/api/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ login: qa, name: "x", role: "operator" }) });
check("повторный login даёт 409", dup.status === 409, String(dup.status));

// админ сам задаёт логин, пароль и роль; пользователь входит с этим паролем
const own = `${qa}.own`;
const ownPassword = "Own-pass-2026-x";
await A.getByPlaceholder("Логин").fill(own);
await A.getByPlaceholder("Имя").fill("Специалист QA");
await A.locator("form.cam-form select").selectOption("specialist");
await A.getByLabel("Пароль", { exact: true }).fill(ownPassword);
await A.getByLabel("сменить при первом входе").uncheck();
await A.locator("form.cam-form").getByRole("button", { name: "Добавить" }).click();
await A.waitForSelector(".notice-ok", { timeout: 5000 }).catch(() => {});
check("после создания со своим паролем он не показывается", (await A.locator(".temp-password").count()) === 0 && (await A.locator(".notice-ok").count()) === 1);
const ownContext = await browser.newContext();
const ownPage = await ownContext.newPage();
await ownPage.goto(BASE + "/");
await ownPage.getByPlaceholder("Логин").fill(own);
await ownPage.getByPlaceholder("Пароль").fill(ownPassword);
await ownPage.getByRole("button", { name: "Войти" }).click();
await ownPage.waitForSelector("nav.tabs button", { timeout: 8000 }).catch(() => {});
check("пользователь входит с паролем, который задал админ, без смены пароля", (await ownPage.locator("nav.tabs button").count()) > 0 && (await ownPage.getByRole("button", { name: /Сменить пароль|Новый пароль/ }).count()) === 0);
await ownContext.close();
check("нет ошибок в консоли у админа", admin.errors.length === 0, admin.errors.join(" | "));

// ---------- специалист: зоны ----------
const spec = await login(browser, "specialist");
const S = spec.page;
await S.locator("nav.tabs button", { hasText: "Зоны" }).click();
await S.waitForSelector(".zone-editor .screen");
const box = await S.locator(".zone-editor .screen").boundingBox();
for (const [fx, fy] of [[0.1, 0.15], [0.45, 0.15], [0.45, 0.6], [0.1, 0.6]]) await S.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
await S.getByPlaceholder("Название").fill("Зона QA");
await S.getByRole("button", { name: "Замкнуть" }).click();
await S.getByRole("button", { name: "Сохранить" }).click();
await S.waitForTimeout(1000);
const zone = ((await api(S, "/api/cameras/cam-1/zones")).body ?? []).find((z) => z.name === "Зона QA");
check("зона нарисована и сохранена", !!zone && zone.polygon.length === 4);
check("координаты зоны в 0..1", !!zone && zone.polygon.flat().every((v) => v >= 0 && v <= 1));
await S.locator(".zone-editor li", { hasText: "Зона QA" }).getByRole("button", { name: "Удалить" }).click();
await S.getByRole("button", { name: "Сохранить" }).click();
await S.waitForTimeout(900);
check("зона удалена и сохранена", !((await api(S, "/api/cameras/cam-1/zones")).body ?? []).some((z) => z.name === "Зона QA"));

// удобство разметки: точки видны, клавиши, замыкание кликом по первой точке, имя по умолчанию
const dots = S.locator(".zone-dot");
// рамку кадра берём заново при каждом клике: раскладка может чуть сдвинуться
const at = async (fx, fy) => {
  const b = await S.locator(".zone-editor .screen").boundingBox();
  await S.mouse.click(b.x + b.width * fx, b.y + b.height * fy);
};
await at(0.1, 0.1);
await at(0.4, 0.1);
await at(0.4, 0.4);
check("поставленные точки видны на кадре", (await dots.count()) === 3);
await S.keyboard.press("Backspace");
check("Backspace убирает последнюю точку", (await dots.count()) === 2);
await S.keyboard.press("Escape");
check("Esc сбрасывает черновик", (await dots.count()) === 0);
await at(0.1, 0.1);
await at(0.4, 0.1);
await at(0.4, 0.4);
check("первая точка подсвечена, когда зону можно замкнуть", (await S.locator(".zone-dot.first.ready").count()) === 1);
await at(0.1, 0.1); // клик по первой точке
check("клик по первой точке замыкает зону с именем по умолчанию", (await S.locator(".zone-list li", { hasText: "Опасная зона 2" }).count()) === 1);
check("на кадре подпись зоны", (await S.locator(".zone-tag", { hasText: "Опасная зона 2" }).count()) === 1);
check("видно, что есть несохранённые изменения", /Не сохранено/.test(await S.locator(".chip").innerText()));
await at(0.6, 0.6);
await at(0.9, 0.6);
await at(0.9, 0.9);
await S.keyboard.press("Enter");
check("Enter замыкает зону", (await S.locator(".zone-list li", { hasText: "Опасная зона 3" }).count()) === 1, `точек ${await dots.count()}; список: ${(await S.locator(".zone-list").innerText()).replace(/\s+/g, " ")}; фокус: ${await S.evaluate(() => document.activeElement?.tagName)}`);
await S.getByRole("button", { name: "Сохранить" }).click();
await S.waitForTimeout(800);
check("после сохранения пометка «Сохранено»", (await S.locator(".chip").innerText()).trim() === "Сохранено");
for (const label of ["Опасная зона 2", "Опасная зона 3"]) {
  await S.locator(".zone-list li", { hasText: label }).getByRole("button", { name: "Удалить" }).click();
}
await S.getByRole("button", { name: "Сохранить" }).click();
await S.waitForTimeout(800);
check("служебные зоны убраны", ((await api(S, "/api/cameras/cam-1/zones")).body ?? []).length === 1);
check("у специалиста нет доступа к пользователям (403)", (await api(S, "/api/users")).status === 403);

// ---------- специалист: ввод нового участка (камера, правила, зона по расписанию) ----------
const areaName = `Участок ${qa}`;
await S.locator("nav.tabs button", { hasText: "Участки" }).click();
await S.getByRole("button", { name: "Новый участок" }).click();
await S.getByPlaceholder("Например, Склад ГСМ").fill(areaName);
await S.getByRole("button", { name: "Вебкамера этого компьютера" }).click();
await S.locator('input[name="new-area-smoking"]').uncheck();
await S.getByRole("button", { name: "Создать участок" }).click();
await S.waitForSelector(".zone-editor .screen", { timeout: 8000 });
const made = ((await api(S, "/api/areas")).body ?? []).find((a) => a.name === areaName);
check("новый участок создан с выбранными правилами", !!made && Array.isArray(made.rules) && made.rules.includes("fall") && !made.rules.includes("smoking"), JSON.stringify(made));
const newCam = made?.cameras?.[0];
check("новая камера привязана к участку", !!newCam);
check("правила камеры берутся с участка", (await api(S, `/api/cameras/${newCam}/rules`)).body?.source === "area");
check("после создания открыт редактор зон этой камеры", (await S.locator('select[aria-label="Камера"]').inputValue()) === newCam);
const frame = await S.locator(".zone-editor .screen").boundingBox();
for (const [fx, fy] of [[0.2, 0.2], [0.6, 0.2], [0.6, 0.7]]) await S.mouse.click(frame.x + frame.width * fx, frame.y + frame.height * fy);
await S.getByPlaceholder("Название").fill("У стеллажей");
await S.getByLabel("по расписанию", { exact: true }).first().check();
await S.getByLabel("Зона действует с").fill("07:55");
await S.getByLabel("Зона действует до").fill("12:00");
await S.getByRole("button", { name: "Замкнуть" }).click();
await S.getByRole("button", { name: "Сохранить" }).click();
await S.waitForTimeout(900);
const timedZone = ((await api(S, `/api/cameras/${newCam}/zones`)).body ?? [])[0];
check("зона сохранена с расписанием 07:55–12:00", timedZone?.schedule?.start === "07:55" && timedZone?.schedule?.end === "12:00", JSON.stringify(timedZone?.schedule));
await S.locator("nav.tabs button", { hasText: "Участки" }).click();
await S.locator(".area-card", { hasText: areaName }).locator(".area-title").click();
await S.getByRole("button", { name: "Правила камеры" }).click();
await S.locator(`input[name="cam-${newCam}-no_helmet"]`).uncheck();
await S.getByRole("button", { name: "Сохранить правила камеры" }).click();
await S.waitForTimeout(700);
const camRules = (await api(S, `/api/cameras/${newCam}/rules`)).body;
check("свои правила камеры важнее правил участка", camRules?.source === "camera" && !camRules.enabled.includes("no_helmet") && camRules.enabled.includes("fall"), JSON.stringify(camRules));
check("специалист не видит адрес камеры", ((await api(S, "/api/cameras")).body ?? []).find((c) => c.id === newCam)?.source === undefined);
await api(admin.page, `/api/cameras/${newCam}`, { method: "DELETE" });
await api(S, `/api/areas/${encodeURIComponent(areaName)}`, { method: "DELETE" });
check("нет ошибок в консоли у специалиста", spec.errors.length === 0, spec.errors.join(" | "));

await browser.close();
finish();
