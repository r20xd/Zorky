import { describe, expect, it } from "vitest";
import {
  areaOptions,
  areaSuffix,
  areasText,
  buildAreasPatch,
  buildCameraBody,
  buildUserCreate,
  filterByArea,
  myAreasLabel,
  parseAreas,
} from "./areas";
import type { Camera, ViolationEvent } from "./types";

const cams: Camera[] = [
  { id: "a", name: "A", area: "Цех 2" },
  { id: "b", name: "B", area: "Цех 1" },
  { id: "c", name: "C" },
];
const ev = (camera_id: string) => ({ id: camera_id, camera_id }) as ViolationEvent;

describe("участки", () => {
  it("разбирает список", () => {
    expect(parseAreas(" Цех 1, Цех 2;Цех 1,, ")).toEqual(["Цех 1", "Цех 2"]);
    expect(areasText(["a", "b"])).toBe("a, b");
    expect(areasText(undefined)).toBe("");
  });
  it("форма камеры: пустой участок не отправляем", () => {
    expect(buildCameraBody({ id: " k1", name: "К", source: "x", area: "  " })).toEqual({ id: "k1", name: "К", source: "x" });
    expect(buildCameraBody({ id: "k1", name: "К", area: " Цех 1 " }).area).toBe("Цех 1");
  });
  it("форма пользователя: areas только у мастера", () => {
    const f = { login: "m", name: "М", role: "master" as const, areas: "Цех 1, Цех 2" };
    expect(buildUserCreate(f).areas).toEqual(["Цех 1", "Цех 2"]);
    expect(buildUserCreate({ ...f, role: "operator" })).not.toHaveProperty("areas");
    expect(buildUserCreate({ ...f, areas: "" })).not.toHaveProperty("areas");
  });
  it("PATCH пустого списка снимает ограничение", () => {
    expect(buildAreasPatch("")).toEqual({ areas: [] });
    expect(buildAreasPatch("Цех 1")).toEqual({ areas: ["Цех 1"] });
  });
  it("подпись мастера", () => {
    expect(myAreasLabel(["Цех 1", "Цех 2"])).toBe("Мои участки: Цех 1, Цех 2");
    expect(myAreasLabel([])).toBe("Мои участки: все");
    expect(myAreasLabel(undefined)).toBe("Мои участки: все");
  });
  it("фильтр и подписи по участку камеры", () => {
    expect(areaOptions(cams)).toEqual(["Цех 1", "Цех 2"]);
    const list = [ev("a"), ev("b"), ev("c")];
    expect(filterByArea(list, cams, "Цех 1").map((e) => e.id)).toEqual(["b"]);
    expect(filterByArea(list, cams, "")).toHaveLength(3);
    expect(areaSuffix(ev("a"), cams)).toBe(" · Цех 2");
    expect(areaSuffix(ev("c"), cams)).toBe("");
  });
});
