import { describe, expect, it } from "vitest";
import { plural } from "./plural";

const T: [string, string, string] = ["тип", "типа", "типов"];

describe("plural", () => {
  it("склоняет по числу", () => {
    expect([1, 2, 5, 11, 12, 14, 21, 22, 25, 111].map((n) => plural(n, T))).toEqual([
      "тип", "типа", "типов", "типов", "типов", "типов", "тип", "типа", "типов", "типов",
    ]);
  });
});
