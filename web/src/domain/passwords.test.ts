import { describe, expect, it } from "vitest";
import { MIN_PASSWORD, generatePassword, validateOptionalPassword } from "./passwords";

describe("passwords", () => {
  it("generates a password of the requested length from the safe alphabet", () => {
    const password = generatePassword(14, (n) => Array.from({ length: n }, (_, i) => i * 37));
    expect(password).toHaveLength(14);
    expect(password).toMatch(/^[a-km-zA-HJ-NP-Z2-9]+$/);
  });

  it("real generator is long enough and differs between calls", () => {
    const a = generatePassword();
    expect(a.length).toBeGreaterThanOrEqual(MIN_PASSWORD);
    expect(generatePassword()).not.toBe(a);
  });

  it("empty is fine, short is not", () => {
    expect(validateOptionalPassword("")).toBeNull();
    expect(validateOptionalPassword("short")).toMatch(/не короче/);
    expect(validateOptionalPassword("long-enough-1")).toBeNull();
  });
});
