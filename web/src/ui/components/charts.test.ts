import { describe, expect, it } from "vitest";
import { labelStep } from "./charts";

describe("labelStep", () => {
  it("на широком графике подписывает чаще, на узком реже", () => {
    expect(labelStep(24, 1200, 36)).toBe(1);
    expect(labelStep(24, 300, 36)).toBe(3);
  });
  it("не меньше минимума и не ломается на одной точке", () => {
    expect(labelStep(24, 1200, 36, 2)).toBe(2);
    expect(labelStep(1, 100)).toBe(1);
  });
});
