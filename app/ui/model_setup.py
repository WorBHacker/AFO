"""The models' settings in the window (stage 8, D39): the panel «Модели» - for each model of the hub, shown in the
picker or not, its own name, what it can do, its prices, the answer's parameters (only those its provider takes) with
the presets «Точно / Баланс / Творчески», the default reasoning depth and a system prompt; the general row holds the
system prompt for every model. A save is a new version; an earlier one can be taken back. «Проверить» asks the model
one short question with these settings: its words, how soon, how fast, what it cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .. import presenter
from .project_rows import local_time

if TYPE_CHECKING:
    from .controller import Controller

GENERAL = "*"  # the general row in the window ('' at the hub)
PROVIDERS = {"llama-swap": "Локальные · llama-swap", "anthropic": "Claude", "claude-code": "Claude Code",
             "openai": "OpenAI", "gemini": "Gemini", "deepseek": "DeepSeek", "openrouter": "OpenRouter"}  # fmt: skip
NUMBERS = ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "max_tokens")
INTEGERS = ("top_k", "max_tokens")
LABELS = {"temperature": ("Температура", "0–2, выше — разнообразнее"), "top_p": ("top_p", "0–1"),
          "top_k": ("top_k", "1–1000"), "min_p": ("min_p", "0–1"), "repeat_penalty": ("Штраф за повтор", "0,5–2"),
          "max_tokens": ("Предел ответа", "токенов")}  # fmt: skip
PRESETS = [("precise", "Точно"), ("balanced", "Баланс"), ("creative", "Творчески")]
PRICES = [("input", "Вход"), ("output", "Выход"), ("cache_read", "Чтение кэша"), ("cache_write", "Запись кэша")]
TRISTATE = ("", "yes", "no")  # as the provider says / yes / no
# which model does what (the general row): the role, its name, what it is for
ROLES = [("chat", "Модель нового чата", "выбирается сама в каждом новом чате"),
         ("cards", "Карточки проектов", "лучше локальная: карточка видит код проекта"),
         ("agents", "Новые агенты", "модель нового профиля агента"),
         ("coordinator", "Координатор", "раздаёт подзадачи другим агентам"),
         ("planner", "Автономный агент: планировщик", "этап 9"),
         ("executor", "Автономный агент: исполнитель", "этап 9"),
         ("vision", "Зрение", "модель с картинками — смотрит на экран; этап 9"),
         ("fallback", "Запасная", "отвечает, если модель упала с ошибкой или отказала")]  # fmt: skip
BLANK: dict[str, Any] = {"name": "", "hidden": False, "context": "", "tools": "", "vision": "", "price_input": "",
                         "price_output": "", "price_cache_read": "", "price_cache_write": "", "temperature": "",
                         "top_p": "", "top_k": "", "min_p": "", "repeat_penalty": "", "max_tokens": "", "stop": "",
                         "thinking": "", "preset": "", "effort": "", "system_prompt": "", "roles": {}}  # fmt: skip


def number(text: str, label: str, *, whole: bool = False) -> float | int | None:
    """A field's number: '' is "not set", a comma is a decimal point; anything else is said back."""
    text = text.strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{label}: «{text}» — не число") from None
    if whole:
        if value != int(value):
            raise ValueError(f"{label}: нужно целое число")
        return int(value)
    return value


def shown(value: Any) -> str:
    """A number as a field shows it: no ".0" on whole ones."""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def draft_of(settings: dict[str, Any]) -> dict[str, Any]:
    """The hub's settings as the panel's fields (text, flags, choices)."""
    caps = settings.get("capabilities") or {}
    prices = settings.get("prices") or {}
    params = settings.get("params") or {}
    out = dict(BLANK)
    out.update({
        "name": settings.get("name", ""),
        "hidden": bool(settings.get("hidden")),
        "context": shown(caps.get("context")),
        "tools": "" if "tools" not in caps else "yes" if caps["tools"] else "no",
        "vision": "" if "vision" not in caps else "yes" if caps["vision"] else "no",
        "stop": "\n".join(params.get("stop") or []),
        "thinking": "" if "thinking" not in params else "on" if params["thinking"] else "off",
        "preset": settings.get("preset", ""),
        "effort": settings.get("effort", ""),
        "system_prompt": settings.get("system_prompt", ""),
        "roles": dict(settings.get("roles") or {}),
    })  # fmt: skip
    for part, _label in PRICES:
        out[f"price_{part}"] = shown(prices.get(part))
    for name in NUMBERS:
        out[name] = shown(params.get(name))
    return out


def settings_of(draft: dict[str, Any], accepts: list[str], *, general: bool = False) -> dict[str, Any]:
    """The fields back as the hub's settings; a field the hub would refuse is said here first (ValueError)."""
    if general:
        out = {"system_prompt": draft["system_prompt"].strip()} if draft["system_prompt"].strip() else {}
        roles = {role: model_id for role, model_id in (draft.get("roles") or {}).items() if model_id}
        return {**out, "roles": roles} if roles else out
    out: dict[str, Any] = {}
    if draft["hidden"]:
        out["hidden"] = True
    if draft["name"].strip():
        out["name"] = draft["name"].strip()
    caps: dict[str, Any] = {}
    context = number(draft["context"], "Контекст", whole=True)
    if context is not None:
        caps["context"] = context
    for cap in ("tools", "vision"):
        if draft[cap] in ("yes", "no"):
            caps[cap] = draft[cap] == "yes"
    if caps:
        out["capabilities"] = caps
    prices = {}
    for part, label in PRICES:
        value = number(draft[f"price_{part}"], f"Цена «{label}»")
        if value is not None:
            prices[part] = value
    if prices:
        out["prices"] = prices
    params: dict[str, Any] = {}
    for name in NUMBERS:
        if name in accepts:
            value = number(draft[name], LABELS[name][0], whole=name in INTEGERS)
            if value is not None:
                params[name] = value
    stops = [line for line in draft["stop"].splitlines() if line]
    if stops and "stop" in accepts:
        params["stop"] = stops
    if draft["thinking"] in ("on", "off") and "thinking" in accepts:
        params["thinking"] = draft["thinking"] == "on"
    if params:
        out["params"] = params
    for key in ("preset", "effort"):
        if draft[key]:
            out[key] = draft[key]
    if draft["system_prompt"].strip():
        out["system_prompt"] = draft["system_prompt"].strip()
    return out


def choices(models: list[dict[str, Any]], current: str) -> list[dict[str, Any]]:
    """The panel's model menu: the general row, then the models by provider (hidden ones say so)."""
    items: list[dict[str, Any]] = [{"id": GENERAL, "name": "Все модели — общий системный промпт", "icon": "layers",
                                    "selected": current == GENERAL}]  # fmt: skip
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for model in presenter.sorted_models(models):
        by_provider.setdefault(model.get("provider") or model["id"].partition("/")[0], []).append(model)
    for provider, group in by_provider.items():
        items.append({"section": PROVIDERS.get(provider, provider), "id": ""})
        for model in group:
            note = " · ".join(s for s in (
                "скрыта в выборе" if model.get("hidden") else "",
                f"своё имя, у поставщика «{model.get('provider_name')}»" if model.get("provider_name")
                and model.get("provider_name") != model["name"] else "",
                f"настройки v{model['settings_version']}" if model.get("settings_version") else "",
            ) if s)  # fmt: skip
            items.append({"id": model["id"], "name": model["name"], "note": note, "selected": model["id"] == current,
                          "icon": "cpu" if model.get("locality") == "local" else "cloud"})  # fmt: skip
    return items


def history_rows(history: list[dict[str, Any]], current: int) -> list[dict[str, Any]]:
    return [{"version": h["version"], "when": local_time(h.get("created_at", "")), "current": h["version"] == current,
             "what": ", ".join(k for k in h.get("settings") or {}) or "всё как у поставщика"}
            for h in history]  # fmt: skip


def check_text(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"Не ответила: {result.get('error') or result.get('text') or 'нет ответа'}"
    parts = [f"«{result.get('text') or '…'}»"]
    if result.get("ttft_s") is not None:
        parts.append(f"первое слово через {result['ttft_s']} с")
    if result.get("tok_s"):
        parts.append(f"{result['tok_s']} ток/с")
    parts.append(f"всего {result.get('seconds', 0)} с")
    cost = result.get("cost_usd") or 0
    parts.append("бесплатно" if not cost else presenter.money(cost))
    return " · ".join(parts)


class ModelSetup(QObject):
    changed = Signal()  # the open model, its draft, its versions, the check
    choicesChanged = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._model = GENERAL
        self._info: dict[str, Any] = {}
        self._draft: dict[str, Any] = dict(BLANK)
        self._saved: dict[str, Any] = dict(BLANK)
        self._error = ""
        self._check = ""
        self._checking = False
        self._loading = False
        app.modelChanged.connect(self.choicesChanged)

    # ================= for QML =================

    @Property(list, notify=choicesChanged)
    def choices(self) -> list:
        return choices(self.app.model_list, self._model)

    @Property(str, notify=changed)
    def modelId(self) -> str:
        return self._model

    @Property(bool, notify=changed)
    def general(self) -> bool:
        return self._model == GENERAL

    @Property(str, notify=changed)
    def title(self) -> str:
        if self._model == GENERAL:
            return "Все модели"
        model = self._info.get("model") or self.app.models.get(self._model) or {}
        return model.get("name") or self._model

    @Property(str, notify=changed)
    def subtitle(self) -> str:
        if self._model == GENERAL:
            return "Общий системный промпт — перед промптом модели и чата"
        model = self._info.get("model") or self.app.models.get(self._model) or {}
        caps = model.get("capabilities") or {}
        parts = [PROVIDERS.get(model.get("provider", ""), model.get("provider", "")), self._model]
        if caps.get("context"):
            parts.append(f"контекст {caps['context'] // 1000}K")
        if model.get("locality") == "cloud" and caps.get("prices_asof"):
            parts.append(f"цены на {caps['prices_asof']}")
        return " · ".join(p for p in parts if p)

    @Property(bool, notify=changed)
    def local(self) -> bool:
        model = self._info.get("model") or {}
        return model.get("locality") == "local"

    @Property(list, notify=changed)
    def accepts(self) -> list:
        return list(self._info.get("accepts") or [])

    @Property(list, notify=changed)
    def numberFields(self) -> list:
        """The answer's numbers this model takes, with their labels and hints."""
        taken = set(self._info.get("accepts") or [])
        return [{"id": n, "name": LABELS[n][0], "hint": LABELS[n][1]} for n in NUMBERS if n in taken]

    @Property(list, notify=changed)
    def priceFields(self) -> list:
        model = self._info.get("model") or {}
        prices = model.get("prices") or {}
        return [{"id": f"price_{part}", "name": label, "hint": f"у поставщика ${prices.get(part, 0):g}"}
                for part, label in PRICES]  # fmt: skip

    @Property(list, notify=changed)
    def roleRows(self) -> list:
        roles = self._draft.get("roles") or {}
        out = []
        for role, name, hint in ROLES:
            model_id = roles.get(role, "")
            model = self.app.models.get(model_id)
            shown = model["name"] if model else (f"{model_id} — сейчас нет" if model_id else "не задана")
            out.append({"id": role, "name": name, "hint": hint, "model": model_id, "modelName": shown})
        return out

    @Property(list, notify=choicesChanged)
    def roleChoices(self) -> list:
        """The models a role may have (Claude Code is an agent of its own: not a role's model)."""
        models = [m for m in presenter.sorted_models(self.app.model_list)
                  if not m["id"].startswith("claude-code/")]  # fmt: skip
        return [{"id": "", "name": "не задана", "icon": "close"}] + [
            {"id": m["id"], "name": m["name"], "icon": "cpu" if m.get("locality") == "local" else "cloud"}
            for m in models]  # fmt: skip

    @Slot(str, str)
    def setRole(self, role: str, model_id: str) -> None:
        if role in dict((r, n) for r, n, _h in ROLES):
            self._draft = {**self._draft, "roles": {**(self._draft.get("roles") or {}), role: model_id}}
            if not model_id:
                self._draft["roles"].pop(role, None)
            self.changed.emit()

    @Property(list, constant=True)
    def presets(self) -> list:
        return [{"id": i, "name": n} for i, n in PRESETS]

    @Property(list, constant=True)
    def efforts(self) -> list:
        return [{"id": "", "name": "как в чате"}] + [{"id": v, "name": label} for v, label in presenter.EFFORTS]

    @Property("QVariantMap", notify=changed)
    def draft(self) -> dict:
        return self._draft

    @Property(bool, notify=changed)
    def dirty(self) -> bool:
        return self._draft != self._saved

    @Property(int, notify=changed)
    def version(self) -> int:
        return int(self._info.get("version") or 0)

    @Property(list, notify=changed)
    def history(self) -> list:
        return history_rows(self._info.get("history") or [], self.version)

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(str, notify=changed)
    def checkText(self) -> str:
        return self._check

    @Property(bool, notify=changed)
    def checking(self) -> bool:
        return self._checking

    @Property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading

    # ================= the hub =================

    def _hub_id(self) -> str:
        return "" if self._model == GENERAL else self._model

    def _show(self, info: dict[str, Any]) -> None:
        self._info = info
        self._saved = draft_of(info.get("settings") or {})
        self._draft = dict(self._saved)
        self._error = ""
        self._loading = False
        self.changed.emit()
        self.choicesChanged.emit()

    @Slot(str)
    def open(self, model_id: str) -> None:
        """A model's settings (GENERAL: the general row; '': the chat's model)."""
        wanted = model_id or self.app.modelId or GENERAL
        if wanted != GENERAL and wanted not in self.app.models:
            wanted = GENERAL
        self._model, self._check, self._loading = wanted, "", True
        hub_id = self._hub_id()
        self.changed.emit()

        def show(info: dict[str, Any]) -> None:
            if self._hub_id() == hub_id:
                self._show(info)

        def failed(error: Exception) -> None:
            self._loading = False
            self._error = f"Настройки не прочитаны: {error}"
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.model_settings(hub_id), show, failed)

    @Slot(str, str)
    def setField(self, name: str, value: str) -> None:
        if name not in BLANK or isinstance(BLANK[name], bool):
            return
        self._draft = {**self._draft, name: value}
        if name in NUMBERS:
            self._draft["preset"] = ""  # a number of one's own: no longer the preset
        self._error = ""
        self.changed.emit()

    @Slot(str, bool)
    def setFlag(self, name: str, value: bool) -> None:
        if isinstance(BLANK.get(name), bool):
            self._draft = {**self._draft, name: value}
            self.changed.emit()

    @Slot(str)
    def applyPreset(self, preset: str) -> None:
        values = (self._info.get("presets") or {}).get(preset)
        if not values:
            return
        taken = set(self._info.get("accepts") or [])
        self._draft = {**self._draft, **{k: shown(v) for k, v in values.items() if k in taken}, "preset": preset}
        self.changed.emit()

    @Slot()
    def discard(self) -> None:
        self._draft = dict(self._saved)
        self._error = ""
        self.changed.emit()

    @Slot()
    def save(self) -> None:
        try:
            settings = settings_of(self._draft, self.accepts, general=self._model == GENERAL)
        except ValueError as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        hub_id = self._hub_id()

        def done(info: dict[str, Any]) -> None:
            if self._hub_id() == hub_id:
                self._show(info)
            self.app.flash(f"Сохранено: «{self.title}», версия {info.get('version')}")
            self.app.load_models()  # the picker shows the new name, hides a hidden one; the roles come again

        def failed(error: Exception) -> None:
            self._error = str(error)
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.save_model_settings(hub_id, settings), done, failed)

    @Slot(int)
    def revert(self, number: int) -> None:
        hub_id = self._hub_id()

        def done(info: dict[str, Any]) -> None:
            if self._hub_id() == hub_id:
                self._show(info)
            self.app.flash(f"Возвращена версия {number} — теперь это версия {info.get('version')}")
            self.app.load_models()

        self.app.call_or_fail(lambda: self.app.client.revert_model_settings(hub_id, number), done,
                              lambda e: self.app.show_error(f"Не возвращена: {e}"))  # fmt: skip

    @Slot()
    def check(self) -> None:
        if self._model == GENERAL or self._checking:
            return
        model_id = self._model
        self._checking, self._check = True, "Спрашиваю модель…" + (" Локальная может загружаться минуту-другую."
                                                                   if self.local else "")  # fmt: skip
        self.changed.emit()

        def done(result: dict[str, Any]) -> None:
            self._checking = False
            if self._model == model_id:
                self._check = check_text(result)
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._checking = False
            self._check = f"Не проверено: {error}"
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.check_model(model_id), done, failed)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "model.settings":
            self.app.load_models()
            mine = (payload.get("model_id") or "") == self._hub_id()
            if mine and not self.dirty and payload.get("version") != self.version and not self._loading:
                self.open(self._model)  # changed elsewhere (another window): show it, unless being edited here
