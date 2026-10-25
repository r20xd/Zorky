import { describe, expect, it } from "vitest";
import { mustChangePassword, parseMe } from "./auth";
import type { User } from "./types";
import { createdLabel, userActions, validatePasswordChange, validateUserForm } from "./users";

const u = (login: string, role: User["role"] = "operator", active = true): User => ({
  login, name: login, role, active, created_at: "2026-01-01T10:00:00Z", created_by: "root",
});

describe("validateUserForm", () => {
  it("принимает верную форму", () => {
    expect(validateUserForm({ login: "ivan", name: "Иван", role: "master" })).toBeNull();
  });
  it("отклоняет пустое и неверное", () => {
    expect(validateUserForm({ login: " ", name: "И", role: "master" })).toMatch(/логин/);
    expect(validateUserForm({ login: "и в", name: "И", role: "master" })).toMatch(/логин/);
    expect(validateUserForm({ login: "a", name: "", role: "master" })).toMatch(/имя/);
    expect(validateUserForm({ login: "a", name: "A", role: "root" })).toMatch(/роль/);
  });
});

describe("validatePasswordChange", () => {
  it("правила", () => {
    expect(validatePasswordChange("", "longpassword1", "longpassword1")).toMatch(/текущий/);
    expect(validatePasswordChange("o", "short", "short")).toMatch(/10/);
    expect(validatePasswordChange("longpassword1", "longpassword1", "longpassword1")).toMatch(/отличаться/);
    expect(validatePasswordChange("o", "longpassword1", "other")).toMatch(/совпадают/);
    expect(validatePasswordChange("o", "longpassword1", "longpassword1")).toBeNull();
  });
});

describe("userActions", () => {
  const all = [u("root", "admin"), u("a"), u("off", "operator", false)];
  it("обычный активный", () => {
    expect(userActions(all[1], "root", all)).toEqual({ resetPassword: true, changeRole: true, disable: true, enable: false });
  });
  it("себя и последнего admin не отключаем", () => {
    const a = userActions(all[0], "root", all);
    expect(a.disable).toBe(false);
    expect(a.changeRole).toBe(false);
  });
  it("последний admin защищён и для другого admin", () => {
    expect(userActions(all[0], "other", all).disable).toBe(false);
    const two = [...all, u("r2", "admin")];
    expect(userActions(two[0], "r2", two).disable).toBe(true);
  });
  it("отключённого можно включить", () => {
    expect(userActions(all[2], "root", all)).toEqual({ resetPassword: false, changeRole: true, disable: false, enable: true });
  });
});

describe("must_change_password", () => {
  it("после входа только смена пароля", () => {
    const me = parseMe({ login: "a", name: "A", role: "admin", must_change_password: true });
    expect(mustChangePassword(me)).toBe(true);
    expect(mustChangePassword(parseMe({ login: "a", name: "A", role: "admin" }))).toBe(false);
    expect(mustChangePassword(null)).toBe(false);
  });
});

describe("createdLabel", () => {
  it("дата неизвестна (1970) — только кто создал", () => {
    expect(createdLabel({ ...u("a"), created_at: "1970-01-01T00:00:00Z" })).toBe("root");
  });
  it("кем и когда", () => {
    expect(createdLabel(u("a"))).toContain("root,");
  });
});
