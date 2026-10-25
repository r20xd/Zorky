import { describe, expect, it } from "vitest";
import { TEMP_PASSWORD_COLORS as C, WCAG_AA, contrastRatio } from "./contrast";

describe("contrastRatio", () => {
  it("чёрное на белом — 21, одинаковые — 1", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#336699", "#336699")).toBeCloseTo(1, 5);
  });
  it("старый баннер (светлый текст на светло-жёлтом) не проходил AA", () => {
    expect(contrastRatio("#e8efe9", "#fff8e1")).toBeLessThan(WCAG_AA);
  });
  it("баннер временного пароля проходит AA: текст и пароль", () => {
    expect(contrastRatio(C.text, C.bg)).toBeGreaterThanOrEqual(WCAG_AA);
    expect(contrastRatio(C.password, C.bg)).toBeGreaterThanOrEqual(WCAG_AA);
  });
});
