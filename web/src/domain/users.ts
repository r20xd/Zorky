import type { Role, User } from "./types";

export const ROLE_LABELS: Record<Role, string> = {
  operator: "Оператор",
  master: "Мастер",
  specialist: "Специалист",
  manager: "Руководитель",
  auditor: "Аудитор",
  admin: "Администратор",
};

export const ROLE_LIST = Object.keys(ROLE_LABELS) as Role[];

/** Проверка формы «Добавить»: текст ошибки или null. */
export function validateUserForm(form: { login: string; name: string; role: string }): string | null {
  const login = form.login.trim();
  if (!login) return "укажите логин";
  if (!/^[A-Za-z0-9._-]+$/.test(login)) return "логин: латиница, цифры, . _ -";
  if (!form.name.trim()) return "укажите имя";
  if (!ROLE_LIST.includes(form.role as Role)) return "выберите роль";
  return null;
}

/** Смена своего пароля: минимум 10 символов, не равен старому, повтор совпадает. */
export function validatePasswordChange(oldPw: string, newPw: string, again: string): string | null {
  if (!oldPw) return "введите текущий пароль";
  if (newPw.length < 10) return "новый пароль не короче 10 символов";
  if (newPw === oldPw) return "новый пароль должен отличаться от старого";
  if (newPw !== again) return "пароли не совпадают";
  return null;
}

export interface UserActions {
  resetPassword: boolean;
  changeRole: boolean;
  disable: boolean;
  enable: boolean;
}

/** Какие действия показывать для строки: себя и последнего активного admin не отключаем и не понижаем. */
export function userActions(user: User, meLogin: string, users: User[]): UserActions {
  const self = user.login === meLogin;
  const lastAdmin =
    user.role === "admin" && user.active && users.filter((u) => u.role === "admin" && u.active).length <= 1;
  const protectedRow = self || lastAdmin;
  return {
    resetPassword: user.active,
    changeRole: !protectedRow,
    disable: user.active && !protectedRow,
    enable: !user.active,
  };
}

/** Строка «кем и когда создан». */
export function createdLabel(user: User): string {
  const d = new Date(user.created_at);
  // бэкенд подставляет 1970-й, если дата создания неизвестна (учётки из файла без даты)
  if (Number.isNaN(d.getTime()) || d.getTime() <= 0) return user.created_by;
  return `${user.created_by}, ${d.toLocaleString("ru-RU")}`;
}
