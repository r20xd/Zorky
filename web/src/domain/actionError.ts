/** Ошибка запроса с кодом ответа (0 — до ответа не дошло: сеть). */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Текст для оператора по ошибке действия (PATCH статуса). */
export function actionErrorText(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 409) return "Мастер не в сети — передайте позже или позвоните";
  if (status === 403) return "Нет прав на это действие";
  if (status === 404) return "Инцидент не найден — возможно, уже удалён";
  if (status === 401) return "Нужно войти заново";
  if (status === 0) return "Нет связи с сервером — проверьте сеть и повторите";
  return `Не удалось выполнить действие (ошибка ${status})`;
}

export const ACTION_ERROR_MS = 10_000;
