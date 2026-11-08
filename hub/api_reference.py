"""The hub's API as a reference (Q5, 2026-10-28): every HTTP endpoint and every event of the WebSocket, in docs/API.md.

Made from the code itself, so it cannot drift: the routes FastAPI knows (their body and query parameters, the first
line of each handler's docstring, who may call it), the event types found where the code publishes them (with the
payload's keys when they are written out in place). The file is made on demand and is not kept in the repository.

    python -m hub.api_reference      (writes docs/API.md)
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute, APIWebSocketRoute

from .api.deps import OUTSIDE_ROUTES

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "API.md"
EVENT_TYPE = re.compile(r"^[a-z][a-z_]*(\.[a-z_*{}]+)+$")
APP_GUARD = re.compile(r"client\.name != APP_CLIENT")
PUBLISHERS = ("publish", "_publish")  # the bus's own, and the helpers some modules wrap it in

# docs/API.md is a document in Russian, for developers: its words are its own, not the window's (10.5c)
INTRO = """\
# API хаба AFO

Справочник сделан из кода командой `python -m hub.api_reference` — не правьте его руками: тест сверяет его с кодом.

## Как подключаться

- Адрес: `http://127.0.0.1:8765` (порт меняет `LOCALCHAT_HUB_PORT`). Хаб отвечает только на запросы к этому
  компьютеру и не принимает запросы со страниц браузера (D31).
- Токен: заголовок `Authorization: Bearer <токен>`. У окна AFO свой токен (`data/tokens/app.token`), у
  внешних клиентов (Cline, Claude Code, расширение VS Code) — свои, их выдаёт окно в «Подключениях». Без токена
  открыт только `GET /v1/health`.
- «Только окно» — запрос, который хаб принимает лишь от окна AFO; другим клиентам он отвечает 403. Другой
  клиент читает проекты так же, как через MCP (облачный — только проекты, где можно облако; без секретных файлов, текст
  через фильтр секретов), а разговоры видит и продолжает только свои; по WebSocket он получает состояние хаба и
  проектов и события своих разговоров (D31, D46).
- Ошибки — JSON `{"detail": ...}`: 401 — нет токена или он неверный, 403 — не тот клиент, 404 — нет такого, 409 —
  конфликт состояния, 422 — неверные данные (у ошибок проверки `detail` — список полей).
- Ответ модели в чате идёт не в ответе на `POST .../messages`, а событиями `chat.*` по WebSocket `/v1/events`.
- MCP для внешних агентов (D32): `/mcp` (Streamable HTTP, с токеном клиента). OpenAI-совместимый вход (D33):
  `http://127.0.0.1:8765/v1/openai`.
"""  # i18n: keep


def _type(annotation: Any) -> str:
    text = annotation.__name__ if isinstance(annotation, type) else str(annotation)
    text = re.sub(r"\b(?:typing|collections\.abc|pydantic(?:\.\w+)*|hub(?:\.\w+)*|common(?:\.\w+)*)\.", "", text)
    return text.replace("NoneType", "None")


def _default(value: Any) -> str:
    return "" if repr(value) == "PydanticUndefined" else f" = {value!r}"


def _first_line(doc: str | None) -> str:
    doc = (doc or "").strip()
    return re.sub(r"\s+", " ", doc.split("\n\n", 1)[0]).replace("|", "\\|")


def _guards(module: Any) -> set[str]:
    """The module's functions that let only the desktop app through (they compare the client with APP_CLIENT)."""
    found = set()
    for name, value in vars(module).items():
        if inspect.isfunction(value) and value.__module__ == module.__name__:
            try:
                if APP_GUARD.search(inspect.getsource(value)):
                    found.add(name)
            except OSError, TypeError:
                continue
    return found


def _calls(dependant: Any) -> set[str]:
    names = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            names.add(getattr(sub.call, "__name__", ""))
        names |= _calls(sub)
    return names


def _who(route: APIRoute, guards: set[str]) -> str:
    calls = _calls(route.dependant)
    if "require_client" not in calls and not route.dependant.dependencies:
        return "без токена"  # i18n: keep
    source = inspect.getsource(route.endpoint)
    helpers = guards - {"require_app", route.endpoint.__name__}  # a route that compares the client itself is no guard
    if "require_app" in calls or any(re.search(rf"\b{g}\(", source) for g in helpers):
        return "только окно"  # i18n: keep
    if not any((method, route.path) in OUTSIDE_ROUTES for method in route.methods):  # require_client's own list (D46)
        return "только окно"  # i18n: keep
    return "токен"  # i18n: keep


def _params(route: APIRoute) -> str:
    said = [f"`?{p.name}: {_type(p.field_info.annotation)}{_default(p.field_info.default)}`"
            for p in route.dependant.query_params]  # fmt: skip
    if route.body_field is not None:
        model = route.body_field.field_info.annotation
        known = getattr(model, "model_fields", None)
        if known:
            body = ", ".join(f"{n}: {_type(f.annotation)}{_default(f.default)}" for n, f in known.items())
            said.append(f"тело `{{{body}}}`")  # i18n: keep
        else:
            said.append(f"тело `{_type(model)}`")  # i18n: keep
    return "<br>".join(said).replace("|", "\\|")


def _routes(app: Any) -> list[tuple[Any, list[Any]]]:
    """(the module, its routes) in the order the hub includes them."""
    groups: dict[str, tuple[Any, list[Any]]] = {}
    for entry in app.routes:
        for route in getattr(getattr(entry, "original_router", None), "routes", [entry]):
            endpoint = getattr(route, "endpoint", None)
            module = sys.modules.get(getattr(endpoint, "__module__", ""), None)
            if module is not None and isinstance(route, APIRoute | APIWebSocketRoute):
                groups.setdefault(module.__name__, (module, []))[1].append(route)
    return list(groups.values())


def endpoints(app: Any) -> str:
    out = ["## Эндпоинты", ""]  # i18n: keep
    for module, routes in _routes(app):
        http = [r for r in routes if isinstance(r, APIRoute)]
        if not http:
            continue
        guards = _guards(module)
        out += [f"### {module.__name__.rsplit('.', 1)[-1]}", "", _first_line(module.__doc__), "",
                "| Метод | Путь | Кто | Что | Параметры |", "|---|---|---|---|---|"]  # fmt: skip  # i18n: keep
        for route in http:
            methods = ", ".join(sorted(route.methods - {"HEAD"}))
            what = _first_line(route.endpoint.__doc__) or f"`{route.endpoint.__name__}()`"  # no docstring: its name
            out.append(f"| {methods} | `{route.path}` | {_who(route, guards)} | {what} | {_params(route)} |")
        out.append("")
    return "\n".join(out)


# --- the events, from where the code publishes them ---


def _keys(node: ast.AST | None) -> list[str]:
    if isinstance(node, ast.Dict):
        return [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return []


def _event_type(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        parts = (v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}" for v in node.values)
        text = "".join(parts)  # f"budget.{kind}" as budget.{kind}
    else:
        return None
    return text if EVENT_TYPE.match(text) else None


def _scan(path: Path, found: dict[str, dict[str, Any]]) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    where = path.relative_to(ROOT).as_posix()

    def note(kind: str, payload: ast.AST | None, stored: bool) -> None:
        entry = found.setdefault(kind, {"keys": [], "stored": False, "where": []})
        entry["keys"] += [k for k in _keys(payload) if k not in entry["keys"]]
        entry["stored"] |= stored
        if where not in entry["where"]:
            entry["where"].append(where)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in PUBLISHERS:
            persist = next((k.value for k in node.keywords if k.arg == "persist"), None)
            stored = not (isinstance(persist, ast.Constant) and persist.value is False)
            kind = _event_type(node.args[0] if node.args else None)
            if kind:
                note(kind, node.args[1] if len(node.args) > 1 else None, stored)
        elif isinstance(node, ast.Tuple) and len(node.elts) == 2 and isinstance(node.elts[1], ast.Dict):
            kind = _event_type(node.elts[0])  # ("port.opened", {...}): published later from a list
            if kind:
                note(kind, node.elts[1], True)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):  # def _publish(self, kind="setup.changed")
            args = node.args.args[len(node.args.args) - len(node.args.defaults) :]
            for arg, default in zip(args, node.args.defaults, strict=True):
                kind = _event_type(default) if arg.arg == "kind" else None
                inner = next((c for c in ast.walk(node) if isinstance(c, ast.Call)
                              and isinstance(c.func, ast.Attribute) and c.func.attr == "publish"), None)  # fmt: skip
                if kind and inner is not None:
                    note(kind, inner.args[1] if len(inner.args) > 1 else None, True)


def event_types() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in sorted((ROOT / "hub").rglob("*.py")):
        _scan(path, found)
    return dict(sorted(found.items()))


def events() -> str:
    from .api import events_ws
    from .events import Event

    envelope = ", ".join(f'"{f.name}"' for f in fields(Event))
    out = ["## WebSocket `/v1/events`", "", "```text", (events_ws.__doc__ or "").strip(), "```", "",
           f"Каждое событие — JSON-объект `{{{envelope}}}`. `id` есть только у событий, которые хаб хранит в журнале "
           "(их можно дочитать после переподключения: `since`, а также `GET /v1/feed`); у живых событий `id` — "
           "`null`.", "", "### Типы событий", "",  # i18n: keep
           "| Тип | Хранится | Поля payload | Где публикуется |", "|---|---|---|---|"]  # fmt: skip  # i18n: keep
    for kind, entry in event_types().items():
        keys = ", ".join(f"`{k}`" for k in entry["keys"]) or "—"
        stored = "да" if entry["stored"] else "нет"  # i18n: keep
        out.append(f"| `{kind}` | {stored} | {keys} | {', '.join(entry['where'])} |")
    out.append("")
    return "\n".join(out)


def render() -> str:
    from common.paths import data_paths

    from .server import create_app
    from .settings import HubSettings

    app = create_app(HubSettings(paths=data_paths()))  # nothing starts until it is served
    return "\n".join([INTRO, endpoints(app), events()])


if __name__ == "__main__":
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_bytes(render().encode("utf-8"))
    print(f"written: {TARGET}")
