"""Командная строка: python -m app.cli add-user <login> <role> --name "Имя" (пароль — getpass)."""

from __future__ import annotations

import argparse
import getpass
import sys

from .domain.users import ROLES, make_record
from .infrastructure import config
from .infrastructure.users_file import load_users, save_users


def add_user(path, login: str, role: str, name: str, password: str) -> None:
    if role not in ROLES:
        raise ValueError(f"роль должна быть одной из: {', '.join(ROLES)}")
    users = load_users(path)
    users[login] = make_record(name, role, password)
    save_users(path, users)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add-user", help="добавить или обновить пользователя")
    add.add_argument("login")
    add.add_argument("role", choices=ROLES)
    add.add_argument("--name", default="")
    args = parser.parse_args(argv)
    password = getpass.getpass("Пароль: ")
    if not password or password != getpass.getpass("Ещё раз: "):
        print("Пароли не совпали или пустые", file=sys.stderr)
        return 1
    path = config.users_path()
    add_user(path, args.login, args.role, args.name or args.login, password)
    print(f"Готово: {args.login} ({args.role}) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
