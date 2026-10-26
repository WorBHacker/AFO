"""Hub command line.

python -m hub                         start the hub (same as `serve`)
python -m hub stop                    stop the running hub gracefully
python -m hub token create NAME       register a client and print its token once
python -m hub token create NAME --local   ...for a client that only works with local models (D32)
python -m hub token list              list clients
python -m hub token revoke NAME       the client's token stops working
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

from common import __version__
from common.logging_setup import setup_logging

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

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
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


def stop(settings: HubSettings) -> int:
    """Asks the running hub to stop, as the app client, and waits until it no longer answers."""
    import httpx

    paths = settings.paths
    try:
        info = json.loads(paths.hub_info.read_text(encoding="utf-8"))
        token = (paths.tokens / f"{APP_CLIENT}.token").read_text(encoding="utf-8").strip()
    except OSError, ValueError:
        print("Хаб не запущен (нет hub.json или токена приложения)", file=sys.stderr)
        return 1
    base = f"http://{info['host']}:{info['port']}"
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
            print(f"Клиент {args.name!r}, {kind}. Токен показывается один раз:\n{token}")
        elif args.action == "list":
            for c in db.call(list_clients):
                state = f"отозван {c.revoked_at}" if c.revoked_at else "активен"
                print(f"{c.name:20} {'облачный' if c.cloud else 'локальный':10} создан {c.created_at}  {state}")
        elif args.action == "revoke":
            if not db.call(lambda conn: revoke_client(conn, args.name)):
                print(f"Активного клиента {args.name!r} нет", file=sys.stderr)
                return 1
            print(f"Токен клиента {args.name!r} отозван")
        return 0
    finally:
        db.close()


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
    actions.add_parser("list", help="list clients")
    revoke = actions.add_parser("revoke", help="stop a client's token from working")
    revoke.add_argument("name")
    args = parser.parse_args(argv)

    settings = HubSettings.from_env()
    if args.command == "token":
        return token_command(settings, args)
    if args.command == "stop":
        return stop(settings)
    return serve(settings)


if __name__ == "__main__":
    raise SystemExit(main())
