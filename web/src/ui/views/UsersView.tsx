import { useCallback, useEffect, useState } from "react";
import { createUser, listUsers, patchUser, resetPassword } from "../../infrastructure/api";
import type { Role, User } from "../../domain/types";
import { areasText, buildAreasPatch, buildUserCreate } from "../../domain/areas";
import { ROLE_LABELS, ROLE_LIST, createdLabel, userActions, validateUserForm } from "../../domain/users";
import { MIN_PASSWORD, generatePassword, validateOptionalPassword } from "../../domain/passwords";

const EMPTY_FORM = { login: "", name: "", role: "operator" as Role, areas: "", password: "", mustChange: true };

export default function UsersView({ meLogin }: { meLogin: string }) {
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<string | null>(null); // «пользователь создан» без показа пароля
  const [temp, setTemp] = useState<{ login: string; password: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    listUsers().then(setUsers).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function run(action: () => Promise<void>) {
    try {
      await action();
      setError("");
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function showTemp(login: string, password: string) {
    setTemp({ login, password });
    setDone(null);
    setCopied(false);
  }

  function setPasswordFor(login: string) {
    const password = window.prompt(`Новый пароль для «${login}» (не короче ${MIN_PASSWORD} символов):`);
    if (!password) return;
    const problem = validateOptionalPassword(password);
    if (problem) return setError(problem);
    void run(async () => {
      await resetPassword(login, { password, must_change_password: true });
      setTemp(null);
      setDone(`Пароль «${login}» заменён. При входе пользователь задаст свой.`);
    });
  }

  function add(e: React.FormEvent) {
    e.preventDefault();
    const problem = validateUserForm(form) ?? validateOptionalPassword(form.password);
    if (problem) return setError(problem);
    void run(async () => {
      const res = await createUser(buildUserCreate(form));
      if (res.temp_password) showTemp(res.user.login, res.temp_password);
      else {
        setTemp(null);
        setDone(`Пользователь «${res.user.login}» создан, роль: ${ROLE_LABELS[res.user.role]}. Пароль задан вами.`);
      }
      setForm(EMPTY_FORM);
    });
  }

  return (
    <section className="cams">
      {error && <p className="error">{error}</p>}
      {done && (
        <p className="notice-ok" role="status">
          {done}
        </p>
      )}
      {temp && (
        <div className="temp-password">
          <p>
            Временный пароль для <b>{temp.login}</b>: <code>{temp.password}</code>
          </p>
          <button
            onClick={() => navigator.clipboard.writeText(temp.password).then(() => setCopied(true))}
          >
            {copied ? "Скопировано" : "Скопировать"}
          </button>{" "}
          <button onClick={() => setTemp(null)}>Закрыть</button>
          <p className="muted">Пароль показан один раз — больше его увидеть нельзя. Передайте его пользователю.</p>
        </div>
      )}
      <table className="cams-table">
        <thead>
          <tr>
            <th>Логин</th>
            <th>Имя</th>
            <th>Роль</th>
            <th>Участки</th>
            <th>Активен</th>
            <th>Создан</th>
            <th>
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const a = userActions(u, meLogin, users);
            return (
              <tr key={u.login}>
                <td>{u.login}</td>
                <td>{u.name}</td>
                <td>
                  {a.changeRole ? (
                    <select
                      aria-label={`Роль ${u.login}`}
                      value={u.role}
                      onChange={(e) => {
                        const role = e.target.value as Role;
                        if (window.confirm(`Сменить роль «${u.login}» на «${ROLE_LABELS[role]}»?`))
                          void run(async () => void (await patchUser(u.login, { role })));
                      }}
                    >
                      {ROLE_LIST.map((r) => (
                        <option key={r} value={r}>
                          {ROLE_LABELS[r]}
                        </option>
                      ))}
                    </select>
                  ) : (
                    ROLE_LABELS[u.role]
                  )}
                </td>
                <td>
                  {u.role === "master" ? (
                    <input
                      key={areasText(u.areas)}
                      defaultValue={areasText(u.areas)}
                      placeholder="все"
                      aria-label={`Участки ${u.login}`}
                      title="Участки через запятую; пусто — все"
                      onBlur={(e) => {
                        if (e.target.value.trim() !== areasText(u.areas))
                          void run(async () => void (await patchUser(u.login, buildAreasPatch(e.target.value))));
                      }}
                    />
                  ) : (
                    "—"
                  )}
                </td>
                <td>{u.active ? "да" : "нет"}</td>
                <td>{createdLabel(u)}</td>
                <td>
                  {a.resetPassword && (
                    <>
                      <button
                        onClick={() => {
                          if (window.confirm(`Сбросить пароль «${u.login}»?`))
                            void run(async () => {
                              const res = await resetPassword(u.login);
                              if (res.temp_password) showTemp(u.login, res.temp_password);
                            });
                        }}
                      >
                        Сбросить пароль
                      </button>{" "}
                      <button onClick={() => setPasswordFor(u.login)}>Задать пароль</button>
                    </>
                  )}{" "}
                  {a.disable && (
                    <button
                      onClick={() => {
                        if (window.confirm(`Отключить пользователя «${u.login}»?`))
                          void run(async () => void (await patchUser(u.login, { active: false })));
                      }}
                    >
                      Отключить
                    </button>
                  )}
                  {a.enable && (
                    <button onClick={() => void run(async () => void (await patchUser(u.login, { active: true })))}>
                      Включить
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <form onSubmit={add} className="cam-form">
        <input placeholder="Логин" value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} />
        <input placeholder="Имя" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <select aria-label="Роль" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
          {ROLE_LIST.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </select>
        {form.role === "master" && (
          <input
            placeholder="Участки через запятую"
            value={form.areas}
            onChange={(e) => setForm({ ...form, areas: e.target.value })}
          />
        )}
        <span className="password-field">
          <input
            placeholder={`Пароль (пусто — сгенерировать)`}
            aria-label="Пароль"
            type={showPassword ? "text" : "password"}
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
          <button
            type="button"
            onClick={() => {
              setForm({ ...form, password: generatePassword() });
              setShowPassword(true);
            }}
          >
            Сгенерировать
          </button>
          <button type="button" aria-pressed={showPassword} onClick={() => setShowPassword(!showPassword)}>
            {showPassword ? "Скрыть" : "Показать"}
          </button>
        </span>
        {form.password && (
          <label>
            <input
              type="checkbox"
              checked={form.mustChange}
              onChange={(e) => setForm({ ...form, mustChange: e.target.checked })}
            />{" "}
            сменить при первом входе
          </label>
        )}
        <button type="submit">Добавить</button>
      </form>
    </section>
  );
}
