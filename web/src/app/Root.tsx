import { useCallback, useEffect, useState } from "react";
import App from "./App";
import {
  changePassword,
  demoLogin,
  fetchMe,
  getDemoUsers,
  login,
  logout,
  setUnauthorizedHandler,
} from "../infrastructure/api";
import { mustChangePassword, screenForPath } from "../domain/auth";
import { LogoutIcon } from "../ui/components/icons";
import { validatePasswordChange } from "../domain/users";
import type { Me } from "../domain/types";

type State = { status: "loading" } | { status: "login" } | { status: "dev" } | { status: "in"; me: Me };

function LoginPage({ onDone }: { onDone: () => void }) {
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [tries, setTries] = useState(0);
  const fail = (message: string) => {
    setError(message);
    setTries((n) => n + 1);
  };
  const [demo, setDemo] = useState<{ login: string; name: string; role: string }[]>([]);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    getDemoUsers().then(setDemo);
  }, []);

  async function enterDemo(loginName: string) {
    try {
      await demoLogin(loginName);
      onDone();
    } catch (err) {
      fail((err as Error).message);
    }
  }

  if (picking)
    return (
      <div className="app">
        <div className="login demo-pick">
          <h1>Демо: выберите роль</h1>
          {demo.map((u) => (
            <button key={u.login} type="button" onClick={() => enterDemo(u.login)}>
              {u.name}
            </button>
          ))}
          {error && <p key={tries} className="error fx-shake">{error}</p>}
          <button type="button" className="link" onClick={() => setPicking(false)}>
            Назад ко входу
          </button>
        </div>
      </div>
    );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await login(user, password);
      onDone();
    } catch (err) {
      fail((err as Error).message);
    }
  }

  return (
    <div className="app">
      <form className="login" onSubmit={submit}>
        <h1>Вход</h1>
        <input placeholder="Логин" value={user} onChange={(e) => setUser(e.target.value)} autoFocus />
        <input
          type="password"
          placeholder="Пароль"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" disabled={!user || !password}>
          Войти
        </button>
        {error && <p key={tries} className="error fx-shake">{error}</p>}
        {demo.length > 0 && (
          <button type="button" className="demo-enter" onClick={() => setPicking(true)}>
            Демо-режим — без пароля
          </button>
        )}
      </form>
    </div>
  );
}

function ChangePasswordPage({ onDone }: { onDone: () => void }) {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [tries, setTries] = useState(0);
  const fail = (message: string) => {
    setError(message);
    setTries((n) => n + 1);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const problem = validatePasswordChange(oldPw, newPw, again);
    if (problem) return fail(problem);
    try {
      await changePassword(oldPw, newPw);
      onDone();
    } catch (err) {
      fail((err as Error).message);
    }
  }

  return (
    <div className="app">
      <form className="login" onSubmit={submit}>
        <h1>Смените пароль</h1>
        <p className="muted">Пароль временный. Задайте свой (не короче 10 символов).</p>
        <input type="password" placeholder="Временный пароль" value={oldPw} onChange={(e) => setOldPw(e.target.value)} autoFocus />
        <input type="password" placeholder="Новый пароль" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
        <input type="password" placeholder="Новый пароль ещё раз" value={again} onChange={(e) => setAgain(e.target.value)} />
        <button type="submit">Сменить пароль</button>
        <button type="button" onClick={() => logout().finally(() => window.location.reload())}>Выйти</button>
        {error && <p key={tries} className="error fx-shake">{error}</p>}
      </form>
    </div>
  );
}

export default function Root() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    fetchMe()
      .then(({ me, dev }) =>
        setState(dev ? { status: "dev" } : me ? { status: "in", me } : { status: "login" }),
      )
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setState({ status: "login" }));
    refresh();
  }, [refresh]);

  if (error) return <p className="error">{error}</p>;
  if (state.status === "loading") return <p className="muted">Загрузка…</p>;
  if (state.status === "login") return <LoginPage onDone={refresh} />;

  if (state.status === "in" && mustChangePassword(state.me)) return <ChangePasswordPage onDone={refresh} />;

  const role = state.status === "in" ? state.me.role : null;
  const userSlot =
    state.status === "in" ? (
      <span className="user">
        <span className="avatar" aria-hidden="true">
          {state.me.name
            .split(/\s+/)
            .map((word) => word[0])
            .join("")
            .slice(0, 2)
            .toUpperCase()}
        </span>
        <span className="user-name">{state.me.name}</span>
        <button
          className="icon-btn"
          aria-label="Выйти"
          title="Выйти"
          onClick={() =>
            logout().finally(() => {
              window.history.pushState(null, "", "/");
              setState({ status: "login" });
            })
          }
        >
          <LogoutIcon />
        </button>
      </span>
    ) : null;

  return (
    <App
      key={state.status === "in" ? state.me.login : "dev"}
      role={role}
      start={screenForPath(window.location.pathname, role)}
      userSlot={userSlot}
      areas={state.status === "in" ? state.me.areas : undefined}
      meLogin={state.status === "in" ? state.me.login : ""}
    />
  );
}
