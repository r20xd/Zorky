import { describe, expect, it } from "vitest";
import {
  allowedScreens,
  canEditZones,
  canManageAreas,
  canManageCameras,
  canMark,
  homeScreen,
  isViewOnly,
  parseMe,
  pathForScreen,
  screenForPath,
} from "./auth";

describe("parseMe", () => {
  it("принимает полный ответ", () => {
    expect(parseMe({ login: "a", name: "Аня", role: "master" })).toEqual({
      login: "a",
      name: "Аня",
      role: "master",
    });
  });
  it("отклоняет мусор и неизвестную роль", () => {
    expect(parseMe(null)).toBeNull();
    expect(parseMe({ login: "a", name: "A", role: "root" })).toBeNull();
    expect(parseMe({ login: "a", role: "master" })).toBeNull();
  });
});

describe("экран по роли", () => {
  it("домашний экран", () => {
    expect(homeScreen("operator")).toBe("monitor");
    expect(homeScreen("master")).toBe("master");
    expect(homeScreen("specialist")).toBe("areas");
    expect(homeScreen("manager")).toBe("analytics");
    expect(homeScreen("auditor")).toBe("journal");
    expect(homeScreen("admin")).toBe("cameras");
    expect(homeScreen(null)).toBe("monitor");
  });
  it("домашний экран разрешён роли", () => {
    for (const r of ["operator", "master", "specialist", "manager", "auditor", "admin"] as const)
      expect(allowedScreens(r)).toContain(homeScreen(r));
  });
  it("/master только мастеру (и режиму разработки)", () => {
    expect(screenForPath("/master", "master")).toBe("master");
    expect(screenForPath("/master/", null)).toBe("master");
    expect(screenForPath("/master", "operator")).toBe("monitor");
    expect(screenForPath("/", "manager")).toBe("analytics");
    expect(pathForScreen("master")).toBe("/master");
    expect(pathForScreen("zones")).toBe("/");
  });
});

describe("аудитор и админ", () => {
  it("auditor: журнал и аналитика, без действий", () => {
    expect(allowedScreens("auditor")).toEqual(["journal", "analytics"]);
    expect(canMark("auditor")).toBe(false);
    expect(canEditZones("auditor")).toBe(false);
    expect(canManageCameras("auditor")).toBe(false);
  });
  it("«Камеры» видит только admin (и dev)", () => {
    expect(allowedScreens("admin")).toContain("cameras");
    expect(allowedScreens(null)).toContain("cameras");
    for (const r of ["operator", "master", "specialist", "manager", "auditor"] as const) {
      expect(allowedScreens(r)).not.toContain("cameras");
      expect(canManageCameras(r)).toBe(false);
    }
    expect(canManageCameras("admin")).toBe(true);
  });
});

describe("права на кнопки", () => {
  it("разметка — operator и master", () => {
    expect(canMark("operator")).toBe(true);
    expect(canMark("master")).toBe(true);
    expect(canMark("manager")).toBe(false);
    expect(canMark("specialist")).toBe(false);
  });
  it("зоны — specialist", () => {
    expect(canEditZones("specialist")).toBe(true);
    expect(canEditZones("operator")).toBe(false);
  });
  it("режим разработки — всё можно", () => {
    expect(canMark(null) && canEditZones(null)).toBe(true);
  });
});

describe("admin: мониторинг и зоны только для просмотра", () => {
  it("экраны admin включают monitor и zones", () => {
    expect(allowedScreens("admin")).toEqual(expect.arrayContaining(["monitor", "zones", "cameras", "users"]));
  });
  it("права на действия не растут", () => {
    expect(canMark("admin")).toBe(false);
    expect(canEditZones("admin")).toBe(false);
  });
  it("isViewOnly: только admin и только monitor/zones", () => {
    expect(isViewOnly("admin", "monitor")).toBe(true);
    expect(isViewOnly("admin", "zones")).toBe(true);
    expect(isViewOnly("admin", "cameras")).toBe(false);
    expect(isViewOnly("operator", "monitor")).toBe(false);
    expect(isViewOnly("specialist", "zones")).toBe(false);
    expect(isViewOnly(null, "zones")).toBe(false);
  });
});

describe("специалист по охране труда", () => {
  it("участки, зоны, журнал и аналитика; камеры и пользователи — нет", () => {
    expect(allowedScreens("specialist")).toEqual(["areas", "zones", "journal", "analytics"]);
    expect(canManageAreas("specialist")).toBe(true);
    expect(canManageAreas("admin")).toBe(true);
    for (const r of ["operator", "master", "manager", "auditor"] as const) expect(canManageAreas(r)).toBe(false);
    expect(canManageCameras("specialist")).toBe(false);
  });
});
