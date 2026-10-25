import { describe, expect, it } from "vitest";
import { ApiError, actionErrorText } from "./actionError";

describe("actionErrorText", () => {
  it("409 — мастер не в сети", () => {
    expect(actionErrorText(new ApiError(409, "x"))).toBe("Мастер не в сети — передайте позже или позвоните");
  });
  it("403 и 404", () => {
    expect(actionErrorText(new ApiError(403, "x"))).toMatch(/Нет прав/);
    expect(actionErrorText(new ApiError(404, "x"))).toMatch(/не найден/);
  });
  it("сеть (TypeError от fetch)", () => {
    expect(actionErrorText(new TypeError("Failed to fetch"))).toMatch(/Нет связи/);
  });
  it("прочее — с кодом", () => {
    expect(actionErrorText(new ApiError(500, "x"))).toMatch(/500/);
  });
});
