"""Hub command line.

python -m hub                         start the hub (same as `serve`)
python -m hub stop                    stop the running hub gracefully
python -m hub token create NAME       register a client and print its token once
python -m hub token create NAME --local   ...for a client that only works with local models (D32)
python -m hub token create NAME --save    ...into data/tokens/NAME.token instead of printing it
python -m hub token list              list clients
python -m hub token revoke NAME       the client's token stops working
python -m hub mcp --client NAME       the stdio adapter to the hub's MCP, for clients that only speak stdio (D32)
python -m hub telegram link           a one-time code: "/link <code>" to your bot links your chat (D21)
python -m hub telegram status | unlink
python -m hub key status              which API keys and tokens are set (never their values)
python -m hub key set NAME            store one in the Windows Credential Manager (typed hidden; the connections'
                                      password first, once there is one)
python -m hub key delete NAME         remove it
python -m hub connections reset       a forgotten password: remove it together with every saved connection
python -m hub db restore              the copies of the database (daily, before migrations); NAME restores one while
                                      the hub is stopped, the database it replaces kept as a copy too
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path

from common import __version__
from common.logging_setup import setup_logging
from common.paths import DataPaths

from . import backup
from .auth import APP_CLIENT, create_client, list_clients, revoke_client
from .db import Database, utc_now
from .secret_filter import clean
from .settings import HubSettings
from .singleton import AlreadyRunning, InstanceLock

EXIT_ALREADY_RUNNING = 3
EXIT_PORT_BUSY = 4
STOP_TIMEOUT_S = 20


def serve(settings: HubSettings) -> int:
    log = setup_logging("hub", redact=clean)
    paths = settings.paths.ensure()
    lock = InstanceLock(paths.hub_lock)
    try:
        lock.acquire()
    except AlreadyRunning:
        log.error("a hub is already running (lock %s); this copy exits", paths.hub_lock)
        return EXIT_ALREADY_RUNNING
    watch_for_crashes(paths)
    try:
        if not _port_free(settings.host, settings.port):
            log.error("port %s:%s is taken by another program", settings.host, settings.port)
            return EXIT_PORT_BUSY
        info = {
            "pid": os.getpid(),
            "host": settings.host,
            "port": settings.port,
            "version": __version__,
            "started_at": utc_now(),
        }
        paths.hub_info.write_text(json.dumps(info, indent=2), encoding="utf-8")
        log.info(
            "AFO hub %s starting on http://%s:%s, data in %s",
            __version__,
            settings.host,
            settings.port,
            paths.root,
        )
        _run_uvicorn(settings)
        return 0
    finally:
        paths.hub_info.unlink(missing_ok=True)
        lock.release()


def watch_for_crashes(paths: DataPaths) -> None:
    """A native crash (in tree-sitter, winpty, SQLite: not in Python) leaves the Python stacks in
    data/logs/hub-crash.log; without it the hub just disappears, and hub.log says nothing (D46)."""
    crash_file = open(paths.logs / "hub-crash.log", "a", encoding="utf-8")  # noqa: SIM115 - open until exit
    faulthandler.enable(crash_file, all_threads=True)


def _run_uvicorn(settings: HubSettings) -> None:
    import uvicorn

    from .server import create_app

    # per-request lines would flood the log: the status monitor asks llama-swap every couple of seconds
    for noisy in ("uvicorn.access", "httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,  # uvicorn's loggers go to our handlers (common.logging_setup)
        ws="websockets-sansio",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    server.run()


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _running_hub(settings: HubSettings) -> tuple[str, str] | None:
    """The running hub's base URL and the app token, from hub.json; None when no hub has written one."""
    paths = settings.paths
    try:
        info = json.loads(paths.hub_info.read_text(encoding="utf-8"))
        token = (paths.tokens / f"{APP_CLIENT}.token").read_text(encoding="utf-8").strip()
    except OSError, ValueError:
        return None
    return f"http://{info['host']}:{info['port']}", token


def stop(settings: HubSettings) -> int:
    """Asks the running hub to stop, as the app client, and waits until it no longer answers."""
    import httpx

    hub = _running_hub(settings)
    if hub is None:
        print("Хаб не запущен (нет hub.json или токена приложения)", file=sys.stderr)
        return 1
    base, token = hub
    try:
        response = httpx.post(f"{base}/v1/shutdown", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    except httpx.HTTPError:
        print(f"Хаб не отвечает на {base}", file=sys.stderr)
        return 1
    if response.status_code != 202:
        print(f"Хаб отказал: {response.status_code} {response.text}", file=sys.stderr)
        return 2
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base}/v1/health", timeout=1)
        except httpx.HTTPError:
            print("Хаб остановлен")
            return 0
        time.sleep(0.2)
    print(f"Хаб не остановился за {STOP_TIMEOUT_S} с", file=sys.stderr)
    return 2


def token_command(settings: HubSettings, args: argparse.Namespace) -> int:
    paths = settings.paths.ensure()
    db = Database(paths.db)
    try:
        backup.migrate_keeping_a_copy(db, paths.backups)
        if args.action == "create":
            try:
                token = db.call(lambda conn: create_client(conn, args.name, cloud=not args.local))
            except sqlite3.IntegrityError:
                print(f"Клиент {args.name!r} уже есть", file=sys.stderr)
                return 1
            kind = "локальный" if args.local else "облачный (D32)"
            if args.save:
                saved = save_token(paths, args.name, token)
                print(f"Клиент {args.name!r}, {kind}. Токен сохранён в {saved}")
                print(f"Переходник stdio для него: python -m hub mcp --client {args.name}")
            else:
                print(f"Клиент {args.name!r}, {kind}. Токен показывается один раз:\n{token}")
        elif args.action == "list":
            for c in db.call(list_clients):
                state = f"отозван {c.revoked_at}" if c.revoked_at else "активен"
                print(f"{c.name:20} {'облачный' if c.cloud else 'локальный':10} создан {c.created_at}  {state}")
        elif args.action == "revoke":
            if not db.call(lambda conn: revoke_client(conn, args.name)):
                print(f"Активного клиента {args.name!r} нет", file=sys.stderr)
                return 1
            if args.name != APP_CLIENT:
                (paths.tokens / f"{args.name}.token").unlink(missing_ok=True)
            print(f"Токен клиента {args.name!r} отозван")
        return 0
    finally:
        db.close()


def save_token(paths: DataPaths, name: str, token: str) -> Path:
    """data/tokens/NAME.token, where the stdio adapter finds it: a client's config then holds no secret."""
    target = paths.tokens / f"{name}.token"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(token, encoding="utf-8")
    os.replace(tmp, target)
    return target


def mcp_command(settings: HubSettings, args: argparse.Namespace) -> int:
    from .mcp_stdio import main as adapter

    setup_logging("hub-mcp", console=False, redact=clean)  # stdout is the protocol's; nothing else may go there
    return adapter(settings.paths.ensure(), args.client)


def telegram_command(settings: HubSettings, args: argparse.Namespace) -> int:
    from .secrets import default_secret_store
    from .telegram import TOKEN, linked_chat, new_link_code, unlink

    paths = settings.paths.ensure()
    db = Database(paths.db)
    try:
        backup.migrate_keeping_a_copy(db, paths.backups)
        store = default_secret_store()
        has_token = bool(store.get(TOKEN))
        if args.action in ("link", "unlink") and not _password_given(store):
            return 1
        if args.action == "link":
            code = db.call(new_link_code)
            if not has_token:
                print("Сначала токен бота: python -m hub key set telegram_bot_token (бота создаёт @BotFather)")
            print(f"Отправьте своему боту в Telegram: /link {code}  (код действует 10 минут)")
        elif args.action == "unlink":
            db.call(unlink)
            print("Чат отвязан: бот больше никому не пишет")
        else:
            chat = db.call(linked_chat)
            print(f"токен: {'задан' if has_token else 'не задан'}; чат: {chat if chat is not None else 'не привязан'}")
        return 0
    finally:
        db.close()


def key_command(settings: HubSettings, args: argparse.Namespace) -> int:
    """API keys and tokens live in the OS secret store (D13); this command never prints a value. An address
    (SearXNG) is set in the window."""
    from .connections import CONNECTIONS, clean_value
    from .secrets import KeyringSecretStore, default_secret_store

    secret = {c.name: c for c in CONNECTIONS if c.kind != "address"}
    store = default_secret_store()
    if args.action == "status":
        for c in secret.values():
            print(f"{c.name:22} {'задан' if store.get(c.name) else 'не задан':9} {c.title}: {c.purpose}")
        return 0
    if args.name not in secret:
        print(f"Неизвестный ключ {args.name!r}. Известные: {', '.join(secret)}", file=sys.stderr)
        return 1
    if not isinstance(store, KeyringSecretStore):
        print("Хранилище секретов Windows недоступно, ключ не сохранён (подробности в логе)", file=sys.stderr)
        return 1
    if not _password_given(store):
        return 1
    if args.action == "set":
        value = _read_secret(f"{secret[args.name].title} — {secret[args.name].purpose} (ввод скрыт): ")
        if not value:
            print("Пустое значение, ничего не сохранено", file=sys.stderr)
            return 1
        try:
            value = clean_value(secret[args.name], value)
        except ValueError as exc:
            print(f"Не сохранено: {exc}", file=sys.stderr)
            return 1
        store.set(args.name, value)
        print(f"Ключ {args.name} сохранён в диспетчере учётных данных Windows")
    else:
        print(f"Ключ {args.name} удалён" if store.delete(args.name) else f"Ключ {args.name} и не был задан")
    _refresh_catalog(settings)
    return 0


def _password_given(store) -> bool:
    """Once the connections have a password (D13), the console asks it too before a change."""
    from .connections import PASSWORD, password_matches

    raw = store.get(PASSWORD)
    if not raw:
        return True
    if not password_matches(json.loads(raw), _read_secret("Пароль подключений (ввод скрыт): ")):
        print("Неверный пароль. Забыли его — python -m hub connections reset", file=sys.stderr)
        return False
    return True


def connections_command(settings: HubSettings, args: argparse.Namespace) -> int:
    """A forgotten password: it goes with every saved connection, so nothing set under it stays behind."""
    from .connections import CONNECTIONS, reset
    from .secrets import KeyringSecretStore, default_secret_store

    store = default_secret_store()
    if not isinstance(store, KeyringSecretStore):
        print("Хранилище секретов Windows недоступно (подробности в логе)", file=sys.stderr)
        return 1
    titles = ", ".join(c.title for c in CONNECTIONS)
    print(f"Будут удалены пароль подключений и все сохранённые подключения ({titles}).")
    if _read_secret("Чтобы продолжить, напишите СБРОС: ").casefold() != "сброс":
        print("Ничего не изменено")
        return 1
    paths = settings.paths.ensure()
    db = Database(paths.db)
    try:
        backup.migrate_keeping_a_copy(db, paths.backups)
        removed = db.call(lambda conn: reset(store, conn))
    finally:
        db.close()
    print(f"Пароль сброшен; удалены: {', '.join(removed) or 'ничего не было задано'}. Новый пароль задаётся в окне")
    _refresh_catalog(settings)
    return 0


def db_command(settings: HubSettings, args: argparse.Namespace) -> int:
    """The copies of the database; with a name, that copy back in its place. Only while no hub runs: its lock is
    held here for the time of the restore, so none starts meanwhile (the window would start one)."""
    paths = settings.paths.ensure()
    found = backup.restorable(paths.backups)
    if not args.name:
        if not found:
            print(f"Копий базы нет ({paths.backups})")
            return 0
        print(f"Копии базы в {paths.backups}, старые выше:")
        for copy in found:
            print(backup.describe(copy))
        print("Восстановить: python -m hub db restore ИМЯ — при остановленном хабе")
        return 0
    chosen = next((p for p in found if p.name == args.name), None)
    if chosen is None:
        print(f"Копии {args.name!r} нет в {paths.backups}", file=sys.stderr)
        return 1
    lock = InstanceLock(paths.hub_lock)
    try:
        lock.acquire()
    except AlreadyRunning:
        print("Хаб работает. Остановите его (python -m hub stop) и закройте окно AFO: оно запускает хаб само",
              file=sys.stderr)  # fmt: skip
        return EXIT_ALREADY_RUNNING
    try:
        kept = backup.restore(chosen, paths.db, paths.backups)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"База не восстановлена: {exc}", file=sys.stderr)
        return 1
    finally:
        lock.release()
    print(f"База восстановлена из {chosen.name}")
    if kept is not None:
        print(f"Прежняя база сохранена как {kept.name}: вернуть её — python -m hub db restore {kept.name}")
    return 0


def _read_secret(prompt: str) -> str:
    """Hidden input at a console; a plain line from a pipe (for scripts)."""
    import getpass

    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass(prompt).strip()
    return sys.stdin.readline().strip() if sys.stdin is not None else ""


def _refresh_catalog(settings: HubSettings) -> None:
    """A running hub re-reads its providers now; otherwise the catalog refreshes when the hub starts."""
    import httpx

    hub = _running_hub(settings)
    if hub is None:
        print("Хаб не запущен: каталог моделей обновится при его запуске")
        return
    base, token = hub
    try:
        response = httpx.post(f"{base}/v1/models/sync", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Хаб не обновил каталог ({exc}); можно повторить: POST /v1/models/sync", file=sys.stderr)
        return
    body = response.json()
    enabled = [m["id"] for m in body.get("models", []) if m.get("enabled")]
    print(f"Каталог хаба обновлён, доступно моделей: {len(enabled)}")
    if body.get("errors"):
        print(f"Ошибки поставщиков: {body['errors']}", file=sys.stderr)


def _utf8_pipes() -> None:
    """Output that goes to a pipe or a file is UTF-8, not the Windows code page; a console is left alone."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _utf8_pipes()
    parser = argparse.ArgumentParser(prog="python -m hub", description="AFO hub")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="start the hub")
    commands.add_parser("stop", help="stop the running hub gracefully")
    token = commands.add_parser("token", help="client tokens (D31)")
    actions = token.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="register a client and print its token once")
    create.add_argument("name")
    create.add_argument("--local", action="store_true", help="the client only works with local models")
    create.add_argument("--save", action="store_true", help="write the token to data/tokens/NAME.token")
    actions.add_parser("list", help="list clients")
    revoke = actions.add_parser("revoke", help="stop a client's token from working")
    revoke.add_argument("name")
    telegram = commands.add_parser("telegram", help="approvals from your Telegram bot (D21)")
    telegram.add_argument("action", choices=("link", "status", "unlink"))
    mcp = commands.add_parser("mcp", help="the stdio adapter to the hub's MCP (D32)")
    mcp.add_argument("--client", required=True, help="the client whose token data/tokens/NAME.token holds")
    key = commands.add_parser("key", help="API keys in the Windows Credential Manager (D13)")
    key_actions = key.add_subparsers(dest="action", required=True)
    key_actions.add_parser("status", help="which keys are set")
    key_set = key_actions.add_parser("set", help="store a key (typed hidden, or one line from a pipe)")
    key_set.add_argument("name")
    key_delete = key_actions.add_parser("delete", help="remove a key")
    key_delete.add_argument("name")
    connections = commands.add_parser("connections", help="the connections' password (D13)")
    connections.add_argument("action", choices=("reset",))
    database = commands.add_parser("db", help="the database's copies (D29, D46)")
    database.add_argument("action", choices=("restore",))
    database.add_argument("name", nargs="?", help="the copy to restore; without it, the list of copies")
    args = parser.parse_args(argv)

    settings = HubSettings.from_env()
    if args.command == "token":
        return token_command(settings, args)
    if args.command == "key":
        return key_command(settings, args)
    if args.command == "connections":
        return connections_command(settings, args)
    if args.command == "db":
        return db_command(settings, args)
    if args.command == "stop":
        return stop(settings)
    if args.command == "mcp":
        return mcp_command(settings, args)
    if args.command == "telegram":
        return telegram_command(settings, args)
    return serve(settings)


if __name__ == "__main__":
    raise SystemExit(main())
