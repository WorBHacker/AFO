"""Hub command line.

python -m hub                         start the hub (same as `serve`)
python -m hub stop                    stop the running hub gracefully
python -m hub token create NAME       register a client and print its token once
python -m hub token create NAME --local   ...for a client that only works with local models (D32)
python -m hub token create NAME --save    ...into data/tokens/NAME.token instead of printing it
python -m hub token list              list clients
python -m hub token revoke NAME       the client's token stops working
python -m hub mcp --client NAME       the stdio adapter to the hub's MCP, for clients that only speak stdio (D32)
python -m hub key status              which API keys are set (never their values)
python -m hub key set NAME            store an API key in the Windows Credential Manager (typed hidden)
python -m hub key delete NAME         remove it
"""

from __future__ import annotations

import argparse
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

from .auth import APP_CLIENT, create_client, list_clients, revoke_client
from .db import Database, migrate, utc_now
from .settings import HubSettings
from .singleton import AlreadyRunning, InstanceLock

EXIT_ALREADY_RUNNING = 3
EXIT_PORT_BUSY = 4
STOP_TIMEOUT_S = 20


def serve(settings: HubSettings) -> int:
    log = setup_logging("hub")
    paths = settings.paths.ensure()
    lock = InstanceLock(paths.hub_lock)
    try:
        lock.acquire()
    except AlreadyRunning:
        log.error("a hub is already running (lock %s); this copy exits", paths.hub_lock)
        return EXIT_ALREADY_RUNNING
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
            "LocalChat hub %s starting on http://%s:%s, data in %s",
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
        db.call(migrate)
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

    setup_logging("hub-mcp", console=False)  # stdout is the protocol's; nothing else may go there
    return adapter(settings.paths.ensure(), args.client)


def key_command(settings: HubSettings, args: argparse.Namespace) -> int:
    """API keys live in the OS secret store (D13); this command never prints a value."""
    from .secrets import KNOWN_SECRETS, KeyringSecretStore, default_secret_store

    store = default_secret_store()
    if args.action == "status":
        for name, label in KNOWN_SECRETS.items():
            print(f"{name:20} {'задан' if store.get(name) else 'не задан':9} {label}")
        return 0
    if args.name not in KNOWN_SECRETS:
        print(f"Неизвестный ключ {args.name!r}. Известные: {', '.join(KNOWN_SECRETS)}", file=sys.stderr)
        return 1
    if not isinstance(store, KeyringSecretStore):
        print("Хранилище секретов Windows недоступно, ключ не сохранён (подробности в логе)", file=sys.stderr)
        return 1
    if args.action == "set":
        value = _read_secret(f"{KNOWN_SECRETS[args.name]} (ввод скрыт): ")
        if not value:
            print("Пустое значение, ничего не сохранено", file=sys.stderr)
            return 1
        store.set(args.name, value)
        print(f"Ключ {args.name} сохранён в диспетчере учётных данных Windows")
    else:
        print(f"Ключ {args.name} удалён" if store.delete(args.name) else f"Ключ {args.name} и не был задан")
    _refresh_catalog(settings)
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
    parser = argparse.ArgumentParser(prog="python -m hub", description="LocalChat hub")
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
    mcp = commands.add_parser("mcp", help="the stdio adapter to the hub's MCP (D32)")
    mcp.add_argument("--client", required=True, help="the client whose token data/tokens/NAME.token holds")
    key = commands.add_parser("key", help="API keys in the Windows Credential Manager (D13)")
    key_actions = key.add_subparsers(dest="action", required=True)
    key_actions.add_parser("status", help="which keys are set")
    key_set = key_actions.add_parser("set", help="store a key (typed hidden, or one line from a pipe)")
    key_set.add_argument("name")
    key_delete = key_actions.add_parser("delete", help="remove a key")
    key_delete.add_argument("name")
    args = parser.parse_args(argv)

    settings = HubSettings.from_env()
    if args.command == "token":
        return token_command(settings, args)
    if args.command == "key":
        return key_command(settings, args)
    if args.command == "stop":
        return stop(settings)
    if args.command == "mcp":
        return mcp_command(settings, args)
    return serve(settings)


if __name__ == "__main__":
    raise SystemExit(main())
