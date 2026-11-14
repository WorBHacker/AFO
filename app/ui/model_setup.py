"""The models' settings in the window (stage 8, D39): the panel «Модели» - for each model of the hub, shown in the
picker or not, its own name, what it can do, its prices, the answer's parameters (only those its provider takes) with
the presets «Точно / Баланс / Творчески», the default reasoning depth and a system prompt; the general row holds the
system prompt for every model. A save is a new version; an earlier one can be taken back. «Проверить» asks the model
one short question with these settings: its words, how soon, how fast, what it cost.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from common.i18n import N_, decimal, tr
from common.llama_memory import RESERVE_GIB, draft_load, estimate, moe_load
from hub.gateway.base import PRICES_FRESH_DAYS

from .. import presenter
from . import rows
from .project_rows import local_time

if TYPE_CHECKING:
    from .controller import Controller

GENERAL = "*"  # the general row in the window ('' at the hub)
PROVIDERS = {"llama-swap": N_("Локальные · llama-swap"), "anthropic": "Claude", "claude-code": "Claude Code",
             "openai": "OpenAI", "gemini": "Gemini", "vertex": "Vertex AI", "deepseek": "DeepSeek",
             "openrouter": "OpenRouter"}  # fmt: skip
NUMBERS = ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "max_tokens")
INTEGERS = ("top_k", "max_tokens")
LABELS = {"temperature": (N_("Температура"), N_("0–2, выше — разнообразнее")), "top_p": ("top_p", "0–1"),
          "top_k": ("top_k", "1–1000"), "min_p": ("min_p", "0–1"),
          "repeat_penalty": (N_("Штраф за повтор"), N_("0,5–2")),
          "max_tokens": (N_("Предел ответа"), N_("токенов"))}  # fmt: skip
PRESETS = [("precise", N_("Точно")), ("balanced", N_("Баланс")), ("creative", N_("Творчески"))]
PRICES = [("input", N_("Вход")), ("output", N_("Выход")), ("cache_read", N_("Чтение кэша")),
          ("cache_write", N_("Запись кэша"))]  # fmt: skip
TRISTATE = ("", "yes", "no")  # as the provider says / yes / no
# «для опытных»: the context's budgets and the tools' portions (D22, hub/limits.py)
BUDGETS = [("map", N_("Карта проектов (L0), токенов")), ("project", N_("Карточка проекта (L1), токенов")),
           ("linked", N_("Связанный проект (L1), токенов")), ("live", N_("Что происходит (L2), токенов")),
           ("notes", N_("Заметки (L4), токенов")), ("file_lines", N_("read_file, строк")),
           ("search_matches", N_("Поиск, совпадений")), ("terminal_lines", N_("Терминал, строк"))]  # fmt: skip
# which model does what (the general row): the role, its name, what it is for
ROLES = [("chat", N_("Модель нового чата"), N_("выбирается сама в каждом новом чате")),
         ("cards", N_("Карточки проектов"), N_("лучше локальная: карточка видит код проекта")),
         ("agents", N_("Новые агенты"), N_("модель нового профиля агента")),
         ("coordinator", N_("Координатор"), N_("раздаёт подзадачи другим агентам")),
         ("planner", N_("Автономный агент: планировщик"), N_("этап 9")),
         ("executor", N_("Автономный агент: исполнитель"), N_("этап 9")),
         ("vision", N_("Зрение"), N_("модель с картинками — смотрит на экран; этап 9")),
         ("judge", N_("Проверка цели"), N_("судья режима «Цель»: только читает и сверяет сделанное с договором; "
                                           "лучше не та же модель, что работает")),
         ("fallback", N_("Запасная"), N_("отвечает, если модель упала с ошибкой или отказала")),
         ("complete", N_("Дописывание кода"), N_("серый текст в редакторе; лучше маленькая локальная, загруженная "
                                                 "рядом с моделью чата — иначе дописывание на паузе")),
         ("edit", N_("Правка в редакторе"), N_("Ctrl+I над выделенным: модель предлагает замену, вы её принимаете "
                                               "или нет"))]  # fmt: skip
BLANK: dict[str, Any] = {"name": "", "hidden": False, "weak_russian": False, "context": "", "tools": "", "vision": "",
                         "price_input": "",
                         "price_output": "", "price_cache_read": "", "price_cache_write": "", "temperature": "",
                         "top_p": "", "top_k": "", "min_p": "", "repeat_penalty": "", "max_tokens": "", "stop": "",
                         "thinking": "", "refusal_vector": "", "preset": "", "effort": "", "system_prompt": "",
                         "roles": {},
                         **{f"budget_{name}": "" for name, _label in BUDGETS}}  # fmt: skip


def number(text: str, label: str, *, whole: bool = False) -> float | int | None:
    """A field's number: '' is "not set", a comma is a decimal point; anything else is said back."""
    text = text.strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(tr("{label}: «{text}» — не число", label=label, text=text)) from None
    if whole:
        if value != int(value):
            raise ValueError(tr("{label}: нужно целое число", label=label))
        return int(value)
    return value


def shown(value: Any) -> str:
    """A number as a field shows it: no ".0" on whole ones."""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def price_words(caps: dict[str, Any], today: date) -> list[str]:
    """What the sheet says of a cloud model's prices (stage 20.1): when they were checked, and old ones marked; a
    promotion and when it ends, an announced change, a promotion that ended since - compare."""
    words = []
    asof = caps.get("prices_asof")
    if asof:
        old = (today - date.fromisoformat(asof)).days > PRICES_FRESH_DAYS
        words.append(tr("цены на {date}", date=asof) + (tr(" — старше {days} дней, сверьте", days=PRICES_FRESH_DAYS)
                                                       if old else ""))  # fmt: skip
    ahead = (caps.get("prices_ahead") or [None])[0]
    if ahead:
        then = tr("${input:g}/${output:g} за 1M", input=ahead["prices"].get("input", 0),
                  output=ahead["prices"].get("output", 0))  # fmt: skip
        words.append(tr("акция до {date}, потом {then}", date=ahead["since"], then=then) if ahead.get("promo_end")
                     else tr("с {date} — {then}", date=ahead["since"], then=then))  # fmt: skip
    if caps.get("promo_ended"):
        words.append(tr("акция кончилась {date} — сверьте цену", date=caps["promo_ended"]))
    return words


def draft_of(settings: dict[str, Any]) -> dict[str, Any]:
    """The hub's settings as the panel's fields (text, flags, choices)."""
    caps = settings.get("capabilities") or {}
    prices = settings.get("prices") or {}
    params = settings.get("params") or {}
    out = dict(BLANK)
    out.update({
        "name": settings.get("name", ""),
        "hidden": bool(settings.get("hidden")),
        "weak_russian": bool(settings.get("weak_russian")),
        "context": shown(caps.get("context")),
        "tools": "" if "tools" not in caps else "yes" if caps["tools"] else "no",
        "vision": "" if "vision" not in caps else "yes" if caps["vision"] else "no",
        "stop": "\n".join(params.get("stop") or []),
        "thinking": "" if "thinking" not in params else "on" if params["thinking"] else "off",
        "refusal_vector": "" if "refusal_vector" not in params else "on" if params["refusal_vector"] else "off",
        "preset": settings.get("preset", ""),
        "effort": settings.get("effort", ""),
        "system_prompt": settings.get("system_prompt", ""),
        "roles": dict(settings.get("roles") or {}),
    })  # fmt: skip
    for part, _label in PRICES:
        out[f"price_{part}"] = shown(prices.get(part))
    for name, _label in BUDGETS:
        out[f"budget_{name}"] = shown((settings.get("context_budget") or {}).get(name))
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
    if draft["weak_russian"]:
        out["weak_russian"] = True
    if draft["name"].strip():
        out["name"] = draft["name"].strip()
    caps: dict[str, Any] = {}
    context = number(draft["context"], tr("Контекст"), whole=True)
    if context is not None:
        caps["context"] = context
    for cap in ("tools", "vision"):
        if draft[cap] in ("yes", "no"):
            caps[cap] = draft[cap] == "yes"
    if caps:
        out["capabilities"] = caps
    prices = {}
    for part, label in PRICES:
        value = number(draft[f"price_{part}"], tr("Цена «{label}»", label=tr(label)))
        if value is not None:
            prices[part] = value
    if prices:
        out["prices"] = prices
    params: dict[str, Any] = {}
    for name in NUMBERS:
        if name in accepts:
            value = number(draft[name], tr(LABELS[name][0]), whole=name in INTEGERS)
            if value is not None:
                params[name] = value
    stops = [line for line in draft["stop"].splitlines() if line]
    if stops and "stop" in accepts:
        params["stop"] = stops
    if draft["thinking"] in ("on", "off") and "thinking" in accepts:
        params["thinking"] = draft["thinking"] == "on"
    if draft["refusal_vector"] in ("on", "off") and "refusal_vector" in accepts:
        params["refusal_vector"] = draft["refusal_vector"] == "on"
    if params:
        out["params"] = params
    budget = {}
    for name, label in BUDGETS:
        value = number(draft[f"budget_{name}"], tr(label), whole=True)
        if value is not None:
            budget[name] = value
    if budget:
        out["context_budget"] = budget
    for key in ("preset", "effort"):
        if draft[key]:
            out[key] = draft[key]
    if draft["system_prompt"].strip():
        out["system_prompt"] = draft["system_prompt"].strip()
    return out


def provider_name(ref: str) -> str:
    return tr(PROVIDERS[ref]) if ref in PROVIDERS else ref


def choices(models: list[dict[str, Any]], current: str) -> list[dict[str, Any]]:
    """The panel's model menu: the general row, then the models by provider (hidden ones say so)."""
    items: list[dict[str, Any]] = [{"id": GENERAL, "name": tr("Все модели — общий системный промпт"), "icon": "layers",
                                    "selected": current == GENERAL}]  # fmt: skip
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for model in presenter.sorted_models(models):
        by_provider.setdefault(model.get("provider") or model["id"].partition("/")[0], []).append(model)
    for provider, group in by_provider.items():
        items.append({"section": provider_name(provider), "id": ""})
        for model in group:
            note = " · ".join(s for s in (
                tr("скрыта в выборе") if model.get("hidden") else "",
                tr("пишет по-русски с ошибками") if model.get("weak_russian") else "",
                tr("своё имя, у поставщика «{name}»", name=model.get("provider_name")) if model.get("provider_name")
                and model.get("provider_name") != model["name"] else "",
                tr("настройки v{version}", version=model["settings_version"]) if model.get("settings_version") else "",
            ) if s)  # fmt: skip
            items.append({"id": model["id"], "name": model["name"], "note": note, "selected": model["id"] == current,
                          "icon": "cpu" if model.get("locality") == "local" else "cloud"})  # fmt: skip
    return items


def history_rows(history: list[dict[str, Any]], current: int) -> list[dict[str, Any]]:
    return [{"version": h["version"], "when": local_time(h.get("created_at", "")), "current": h["version"] == current,
             "what": ", ".join(k for k in h.get("settings") or {}) or tr("всё как у поставщика")}
            for h in history]  # fmt: skip


CACHE_CHOICES = ("f16", "q8_0", "q4_0")
# «Быстрее ↔ Качественнее» (10.6): the launch settings in plain words - how much the model remembers of a
# conversation and how exactly; the memory estimate below says whether it fits
LAUNCH_PRESETS = {"fast": {"context": 8192, "cache_type": "q8_0", "gpu_mode": "all"},
                  "balanced": {"context": 32768, "cache_type": "q8_0", "gpu_mode": "all"},
                  "quality": {"context": 65536, "cache_type": "f16", "gpu_mode": "all"}}  # fmt: skip


def preset_draft(preset: str, draft: dict[str, str], trained: int | None) -> dict[str, str]:
    """The launch fields for a preset: its context (no longer than the model was trained for), cache and layers."""
    chosen = LAUNCH_PRESETS[preset]
    context = min(chosen["context"], trained) if trained else chosen["context"]
    return {**draft, "context": str(context), "cache_type": chosen["cache_type"], "gpu_mode": chosen["gpu_mode"],
            "gpu_n": ""}  # fmt: skip


def preset_of(draft: dict[str, str], trained: int | None) -> str:
    return next((p for p in LAUNCH_PRESETS if draft and preset_draft(p, draft, trained) == draft), "")


def launch_draft(view: dict[str, Any]) -> dict[str, str]:
    """A llama-swap model's launch settings as the panel's fields."""
    layers = view.get("gpu_layers", "all")
    return {"context": str(view.get("context") or ""), "gpu_mode": layers if layers in ("all", "auto") else "n",
            "gpu_n": "" if layers in ("all", "auto") else str(layers), "flash_attn": view.get("flash_attn", "auto"),
            "cache_type": view.get("cache_type", "f16"), "parallel": str(view.get("parallel") or 1),
            "ttl_min": shown(round(view.get("ttl", 0) / 60, 1))}  # fmt: skip


def launch_changes(draft: dict[str, str], view: dict[str, Any]) -> dict[str, Any]:
    """The fields back as the hub's changes - only what differs from the config now (ValueError when unreadable)."""
    out: dict[str, Any] = {}
    ttl = number(draft["ttl_min"], tr("Выгружать через"), whole=False)
    ttl_s = round((ttl or 0) * 60)
    if ttl_s != view.get("ttl", 0):
        out["ttl"] = ttl_s
    if not view.get("editable"):
        return out
    context = number(draft["context"], tr("Контекст"), whole=True)
    if context and context != view.get("context"):
        out["context"] = context
    layers: str | int = draft["gpu_mode"]
    if layers == "n":
        layers = number(draft["gpu_n"], tr("Слои на видеокарте"), whole=True) or 0
    if layers != view.get("gpu_layers"):
        out["gpu_layers"] = layers
    for key in ("flash_attn", "cache_type"):
        if draft[key] != view.get(key):
            out[key] = draft[key]
    parallel = number(draft["parallel"], tr("Параллельных запросов"), whole=True)
    if parallel and parallel != view.get("parallel"):
        out["parallel"] = parallel
    return out


ESTIMATE_TONES = {"over": "error", "split": "warn", "auto": "warn"}  # the estimate's verdict: the text's colour


def launch_judged(draft: dict[str, str], view: dict[str, Any], vram_total_mib: int | None,
                  reserve_gib: float | None = None) -> tuple[str, str]:  # fmt: skip
    """What the fields would take of the video memory and RAM, said before anything is written - and its tone
    ('error': it does not fit, 'warn': slower, '': fine or unknown)."""
    info = view.get("info")
    if not view.get("editable"):
        return "", ""
    if not info:
        return tr("Оценки памяти нет: {reason}", reason=view.get("file_error") or tr("файл модели не прочитан")), ""
    try:
        context = number(draft["context"], tr("Контекст"), whole=True) or view.get("context") or 4096
        layers: str | int = draft["gpu_mode"]
        if layers == "n":
            layers = number(draft["gpu_n"], tr("Слои"), whole=True) or 0
    except ValueError as exc:
        return str(exc), ""
    found = estimate(info, context=int(context), cache_type=draft["cache_type"], gpu_layers=layers,
                     mmproj_bytes=view.get("mmproj_bytes") or 0, mmproj_on_gpu=bool(view.get("mmproj_on_gpu")),
                     vram_total_mib=vram_total_mib, reserve_gib=RESERVE_GIB if reserve_gib is None else reserve_gib,
                     draft=draft_load(view), moe=moe_load(view))  # the draft (42.2), experts in RAM (44.1)  # fmt: skip
    return found["text"], ESTIMATE_TONES.get(found["verdict"], "")


def launch_estimate(draft: dict[str, str], view: dict[str, Any], vram_total_mib: int | None,
                    reserve_gib: float | None = None) -> str:  # fmt: skip
    return launch_judged(draft, view, vram_total_mib, reserve_gib)[0]


def check_text(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return tr("Не ответила: {error}", error=result.get("error") or result.get("text") or tr("нет ответа"))
    parts = [tr("«{text}»", text=result.get("text") or "…")]
    if result.get("ttft_s") is not None:
        parts.append(tr("первое слово через {seconds} с", seconds=result["ttft_s"]))
    if result.get("tok_s"):
        parts.append(tr("{speed} ток/с", speed=result["tok_s"]))
    parts.append(tr("всего {seconds} с", seconds=result.get("seconds", 0)))
    cost = result.get("cost_usd") or 0
    parts.append(tr("бесплатно") if not cost else presenter.money(cost))
    return " · ".join(parts)


def speed(measured: dict[str, Any] | None, key: str) -> str:
    value = (measured or {}).get(key)
    return f"{value:.0f}" if isinstance(value, int | float) else "?"


def settings_words(settings: dict[str, Any]) -> str:
    """What the tuning wrote, in words: flash attention, the KV cache, ubatch, threads."""
    parts = []
    if settings.get("flash_attn"):
        parts.append(tr("flash attention вкл") if settings["flash_attn"] == "on" else tr("flash attention выкл"))
    if settings.get("cache_type"):
        parts.append(tr("KV-кэш {type}", type=settings["cache_type"]))
    if settings.get("ubatch"):
        parts.append(f"ubatch {settings['ubatch']}")
    if settings.get("threads"):
        parts.append(f"{settings['threads']} {presenter.plural(settings['threads'], 'поток', 'потока', 'потоков')}")
    return ", ".join(parts)


# what guessing ahead the tuning chose (hub/tuning.py spec_variants), in words
SPEC_KINDS = {"draft": N_("черновая модель {draft}"),
              "draft_fewer": N_("черновая модель {draft}, часть слоёв модели в ОЗУ"),
              "ngram": N_("ngram (повторы уже написанного)")}  # fmt: skip


def spec_text(state: dict[str, Any], model_id: str, launch: dict[str, Any], drafting: dict[str, Any]
              ) -> tuple[str, str]:  # fmt: skip
    """The models view's line about guessing ahead (42.6) and its tone: the draft model on its way (the hub's
    `drafting`), what the tuning found - while the model's command is as it left it - or what the command has."""
    if drafting.get("model_id") == model_id:
        if drafting.get("error"):
            return tr("Черновая модель не скачалась: {error}", error=drafting["error"]), "error"
        if drafting.get("total"):
            return tr("Качаю черновую модель {name}: {done} из {total}…", name=drafting.get("name") or "",
                      done=rows.mb(drafting.get("done") or 0), total=rows.mb(drafting["total"])), ""  # fmt: skip
    spec = ((state.get("results") or {}).get(model_id) or {}).get("spec") or {}
    current = launch.get("spec") or {}
    kinds = current.get("type") or ""
    if spec.get("status") == "unsupported":
        return tr("Эта сборка llama.cpp не умеет угадывать вперёд — обновите её в мастере первого запуска."), ""
    if spec.get("status") == "failed":
        return tr("Ускорение не подобрано: {error}", error=spec.get("error") or ""), "error"
    if spec.get("status") in ("better", "same") and (spec.get("kind") == "none") == (not kinds):
        if spec["kind"] == "none":
            text = tr("Угадывание вперёд не дало выигрыша — выключено (ответ {speed} ток/с).",
                      speed=f"{spec['before']:.0f}")  # fmt: skip
            short = spec.get("short") or {}
            if short.get("short_gb") and short.get("context"):
                text += " " + tr("Черновой модели не хватило {gb} ГБ видеопамяти; с контекстом {context} она "
                                 "поместится — его можно уменьшить выше.", gb=decimal(f"{short['short_gb']:.1f}"),
                                 context=presenter.tokens_words(short["context"]))  # fmt: skip
            elif short.get("short_gb"):
                text += " " + tr("Черновой модели не хватило {gb} ГБ видеопамяти.",
                                 gb=decimal(f"{short['short_gb']:.1f}"))  # fmt: skip
            return text, ""
        what = tr(SPEC_KINDS[spec["kind"]], draft=spec.get("draft") or "")
        text = tr("Ускорение — {what}: ответ {before} → {after} ток/с", what=what, before=f"{spec['before']:.0f}",
                  after=f"{spec['after']:.0f}")  # fmt: skip
        if spec.get("accepted") is not None and spec["kind"] != "ngram":
            text += tr(", угадано {share} %", share=round(spec["accepted"] * 100))
        return text + ".", "ok"
    if kinds:
        draft = re.split(r"[\\/]", current.get("draft") or "")[-1]
        if draft:
            return tr("Сейчас: черновая модель {draft}.", draft=draft), ""
        return tr("Сейчас: {kinds}.", kinds="ngram" if kinds.startswith("ngram") else kinds), ""
    return tr("Угадывание вперёд не настроено. «Ускорить» скачает черновую модель, если она есть для этой модели, и "
              "замерит, что быстрее."), ""  # fmt: skip


def tune_text(state: dict[str, Any], model_id: str) -> str:
    """The models view's line about the tuning of this model: going on, waiting, or what it found."""
    job = state.get("job") or {}
    mine = job.get("model_id") == model_id
    if mine and job.get("status") == "measuring" and job.get("step") == "spec":
        return tr("Меряю угадывание вперёд: {done} из {total}… На это время модели выгружены из видеопамяти.",
                  done=job.get("done", 0), total=job.get("total") or "?")  # fmt: skip
    if mine and job.get("status") == "measuring":
        return tr("Меряю: {done} из {total}… На это время модели выгружены из видеопамяти.", done=job.get("done", 0),
                  total=job.get("total") or "?")  # fmt: skip
    if mine and job.get("status") == "checking":
        return tr("Проверяю, встаёт ли модель с новыми настройками и всем контекстом…")
    if mine and job.get("status") == "waiting":
        return tr("Жду, пока модели освободятся: идёт разговор.")
    if model_id in (job.get("queue") or []):
        return tr("В очереди на замер.")
    result = (state.get("results") or {}).get(model_id)
    if not result:
        return tr("AFO замерит на этом компьютере несколько настроек (llama-bench той же сборки llama.cpp) и запишет "
                  "самые быстрые. Модель на это время выгружается — несколько минут.")  # fmt: skip
    when = local_time(result.get("at", ""))
    if result.get("status") == "better":
        return tr("Подобрано {when}: ответ {before} → {after} ток/с, чтение подсказки {pp_before} → {pp_after} ток/с "
                  "({settings}).", when=when, before=speed(result.get("before"), "tg"),
                  after=speed(result.get("after"), "tg"), pp_before=speed(result.get("before"), "pp"),
                  pp_after=speed(result.get("after"), "pp"),
                  settings=settings_words(result.get("settings") or {}))  # fmt: skip
    if result.get("status") == "same":
        return tr("Замерено {when}: эти настройки и так самые быстрые — ответ {speed} ток/с.", when=when,
                  speed=speed(result.get("after"), "tg"))  # fmt: skip
    return tr("Не подобрано ({when}): {error}", when=when, error=result.get("error") or "")


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
        self._check_failed = False  # the check said no: the sheet shows it red
        self._checking = False
        self._loading = False
        self._launch: dict[str, Any] = {}  # this model in llama-swap's config (local models), and the memory it has
        self._tune: dict[str, Any] = {}  # launch settings by measurement (15.2): the job, each model's result, auto
        self._drafting: dict[str, Any] = {}  # a draft model on its way (42.5): the hub's `drafting`
        self._told: set[str] = set()  # the results already said in the status line
        self._launch_draft: dict[str, str] = {}
        self._vram_mib: int | None = None
        self._reserve_gib: float | None = None  # the video memory a model may not count on, the hub's (38.3)
        self._launch_status = ""
        self._launch_failed = False  # the launch's status says it went wrong: red
        self._launch_asked = False  # llama-swap's config is being read for this model (stage 40: where its context is)
        self._launch_busy = False
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
            return tr("Все модели")
        model = self._info.get("model") or self.app.models.get(self._model) or {}
        return model.get("name") or self._model

    @Property(str, notify=changed)
    def subtitle(self) -> str:
        if self._model == GENERAL:
            return tr("Общий системный промпт — перед промптом модели и чата")
        model = self._info.get("model") or self.app.models.get(self._model) or {}
        caps = model.get("capabilities") or {}
        parts = [provider_name(model.get("provider", "")), self._model]
        if caps.get("context"):
            parts.append(tr("контекст {size}K", size=caps["context"] // 1000))
        if model.get("locality") == "cloud":
            parts += price_words(caps, date.today())
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
        return [{"id": n, "name": tr(LABELS[n][0]), "hint": tr(LABELS[n][1])} for n in NUMBERS if n in taken]

    @Property(list, notify=changed)
    def priceFields(self) -> list:
        model = self._info.get("model") or {}
        prices = model.get("prices") or {}
        return [{"id": f"price_{part}", "name": tr(label),
                 "hint": tr("у поставщика ${price:g}", price=prices.get(part, 0))}
                for part, label in PRICES]  # fmt: skip

    # ---------- a local model's launch in llama-swap (8.5) ----------

    @Property("QVariantMap", notify=changed)
    def launch(self) -> dict:
        view = self._launch
        return {"engine": view.get("engine", ""), "editable": bool(view.get("editable")),
                "profile": view.get("profile", ""), "file": view.get("file", ""), "mmproj": view.get("mmproj", ""),
                "problem": view.get("problem", "")} if view else {}  # fmt: skip

    @Property("QVariantMap", notify=changed)
    def launchDraft(self) -> dict:
        return self._launch_draft

    @Property(str, notify=changed)
    def launchPreset(self) -> str:
        return preset_of(self._launch_draft, (self._launch.get("info") or {}).get("context_trained"))

    @Slot(str)
    def applyLaunchPreset(self, preset: str) -> None:
        if self._launch_draft and preset in LAUNCH_PRESETS:
            trained = (self._launch.get("info") or {}).get("context_trained")
            self._launch_draft = preset_draft(preset, self._launch_draft, trained)
            self._launch_status, self._launch_failed = "", False
            self.changed.emit()

    @Property(bool, notify=changed)
    def launchDirty(self) -> bool:
        if not self._launch:
            return False
        try:
            return bool(launch_changes(self._launch_draft, self._launch))
        except ValueError:
            return True

    @Property(str, notify=changed)
    def launchEstimate(self) -> str:
        return (
            launch_estimate(self._launch_draft, self._launch, self._vram_mib, self._reserve_gib) if self._launch else ""
        )

    @Property(str, notify=changed)
    def launchEstimateTone(self) -> str:
        """'error': it does not fit, 'warn': slower, '' - the sheet colours the estimate by it, not by its words."""
        return (
            launch_judged(self._launch_draft, self._launch, self._vram_mib, self._reserve_gib)[1]
            if self._launch
            else ""
        )

    @Property(str, notify=changed)
    def launchStatus(self) -> str:
        return self._launch_status

    @Property(bool, notify=changed)
    def launchFailed(self) -> bool:
        return self._launch_failed

    @Property(bool, notify=changed)
    def launchBusy(self) -> bool:
        return self._launch_busy

    @Property(list, constant=True)
    def cacheChoices(self) -> list:
        return [{"id": c, "name": c} for c in CACHE_CHOICES]

    def _load_launch(self, model_id: str) -> None:
        model = self.app.models.get(model_id) or {}
        self._launch, self._launch_draft, self._launch_status, self._launch_failed = {}, {}, "", False
        self._launch_asked = model.get("provider", model_id.partition("/")[0]) == "llama-swap"
        if not self._launch_asked:
            return
        ref = model_id.partition("/")[2]

        def show(listing: dict[str, Any]) -> None:
            if self._model != model_id:
                return
            self._launch = next((m for m in listing.get("models") or [] if m["id"] == ref), {})
            self._launch_draft = launch_draft(self._launch) if self._launch else {}
            self._vram_mib = listing.get("vram_total_mib")
            self._reserve_gib = listing.get("vram_reserve_gib")
            self._launch_asked = False
            self.changed.emit()

        def failed(_error: Exception) -> None:
            if self._model == model_id:
                self._launch_asked = False
                self.changed.emit()

        self.app._call(lambda: self.app.client.llama_models(), show, failed)

    @Slot(str, str)
    def setLaunch(self, name: str, value: str) -> None:
        if self._launch_draft and name in self._launch_draft:
            self._launch_draft = {**self._launch_draft, name: value}
            self._launch_status, self._launch_failed = "", False
            self.changed.emit()

    @Slot()
    def saveLaunch(self) -> None:
        try:
            changes = launch_changes(self._launch_draft, self._launch)
        except ValueError as exc:
            self._launch_status, self._launch_failed = str(exc), False
            self.changed.emit()
            return
        if not changes:
            return
        model_id, ref = self._model, self._launch["id"]
        self._launch_busy, self._launch_failed = True, False
        self._launch_status = tr("Записываю в config.yaml и жду, что llama-swap его перечитает…")
        self.changed.emit()

        def done(result: dict[str, Any]) -> None:
            self._launch_busy = False
            if self._model == model_id:
                self._launch = next((m for m in result.get("models") or [] if m["id"] == ref), self._launch)
                self._launch_draft = launch_draft(self._launch)
            if result.get("verified"):
                self._launch_status = tr(
                    "Записано, llama-swap перечитал конфиг. Копия прежнего — {backup}. «Проверить "
                    "запуск» загрузит модель с новыми настройками.",
                    backup=result.get("backup"),
                )
            else:
                self._launch_status = tr("Записано, но не проверено: {problem}. Копия прежнего — {backup}. «Проверить "
                                         "запуск» загрузит модель с новыми настройками.", problem=result.get("problem"),
                                         backup=result.get("backup"))  # fmt: skip
            self._launch_failed = False
            self.changed.emit()
            self.app.load_models()

        def failed(error: Exception) -> None:
            self._launch_busy = False
            self._launch_status, self._launch_failed = tr("Не записано: {error}", error=error), True
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.change_llama_model(ref, changes), done, failed)

    @Slot()
    def trialLaunch(self) -> None:
        if not self._launch or self._launch_busy:
            return
        model_id, ref = self._model, self._launch["id"]
        self._launch_busy, self._launch_failed = True, False
        self._launch_status = tr("Загружаю модель с этими настройками и задаю короткий вопрос — может занять минуты…")
        self.changed.emit()

        def done(result: dict[str, Any]) -> None:
            self._launch_busy = False
            said = check_text(result)
            if result.get("over"):  # it answered from the shared memory the video card's driver lent it (stage 28)
                said += tr(". Не поместилась в видеопамять и ответила из общей памяти — в разы медленнее. Оценка: "
                           "{estimate}", estimate=result["over"])  # fmt: skip
            if result.get("restored"):
                said += tr(". Модель не запустилась — конфиг возвращён из {backup}", backup=result["restored"])
                if self._model == model_id:
                    self._load_launch(model_id)
            self._launch_status = said
            self._launch_failed = not result.get("ok") or bool(result.get("restored") or result.get("over"))
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._launch_busy = False
            self._launch_status, self._launch_failed = tr("Не проверено: {error}", error=error), True
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.trial_llama_model(ref), done, failed)

    @Property(list, notify=changed)
    def roleRows(self) -> list:
        roles = self._draft.get("roles") or {}
        out = []
        for role, name, hint in ROLES:
            model_id = roles.get(role, "")
            model = self.app.models.get(model_id)
            shown = model["name"] if model else (tr("{model} — сейчас нет", model=model_id) if model_id
                                                 else tr("не задана"))  # fmt: skip
            out.append({"id": role, "name": tr(name), "hint": tr(hint), "model": model_id, "modelName": shown})
        return out

    @Property(list, notify=choicesChanged)
    def roleChoices(self) -> list:
        """The models a role may have (Claude Code is an agent of its own: not a role's model)."""
        models = [m for m in presenter.sorted_models(self.app.model_list)
                  if not m["id"].startswith("claude-code/")]  # fmt: skip
        return [{"id": "", "name": tr("не задана"), "icon": "close"}] + [
            {"id": m["id"], "name": m["name"], "icon": "cpu" if m.get("locality") == "local" else "cloud"}
            for m in models]  # fmt: skip

    @Slot(str, str)
    def setRole(self, role: str, model_id: str) -> None:
        if role in dict((r, n) for r, n, _h in ROLES):
            self._draft = {**self._draft, "roles": {**(self._draft.get("roles") or {}), role: model_id}}
            if not model_id:
                self._draft["roles"].pop(role, None)
            self.changed.emit()

    @Property(list, notify=changed)
    def budgetFields(self) -> list:
        """The context's budgets with the hub's defaults as hints."""
        limits = self._info.get("limits") or {}
        return [{"id": f"budget_{name}", "name": tr(label),
                 "hint": tr("{value} по умолчанию", value=limits[name]["default"]) if name in limits else ""}
                for name, label in BUDGETS]  # fmt: skip

    @Slot(str)
    def exportTo(self, url: str) -> None:
        """Every model's settings into a JSON file (the window writes it)."""
        path = Path(QUrl(url).toLocalFile() or url)

        def write(data: dict[str, Any]) -> None:
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                self.app.show_error(tr("Файл не записан: {error}", error=exc))
                return
            count = len(data.get("models") or {})
            whose = presenter.plural(count, "модели", "моделей", "моделей")
            self.app.flash(tr("Настройки {count} {models} записаны в {file}", count=count, models=whose,
                              file=path.name))  # fmt: skip

        self.app.call_or_fail(lambda: self.app.client.export_model_settings(), write,
                              lambda e: self.app.show_error(tr("Не выгружено: {error}", error=e)))  # fmt: skip

    @Slot(str)
    def importFrom(self, url: str) -> None:
        path = Path(QUrl(url).toLocalFile() or url)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.app.show_error(tr("Файл не прочитан: {error}", error=exc))
            return

        def done(report: dict[str, Any]) -> None:
            said = tr("Загружено: {count}", count=len(report.get("saved") or []))
            if report.get("skipped"):
                said += tr("; пропущено (нет в этом хабе): {models}", models=", ".join(report["skipped"]))
            if report.get("errors"):
                said += tr("; ошибки: {errors}", errors="; ".join(f"{k} — {v}" for k, v in report["errors"].items()))
            self.app.flash(said, error=bool(report.get("errors")), ms=15000)
            self.app.load_models()
            self.open(self._model)

        self.app.call_or_fail(lambda: self.app.client.import_model_settings(data), done,
                              lambda e: self.app.show_error(tr("Не загружено: {error}", error=e)))  # fmt: skip

    # --- launch settings by measurement (15.2) ---

    def _tune_id(self) -> str:
        return self._launch.get("id") or ""

    @Property(str, notify=changed)
    def tuneText(self) -> str:
        return tune_text(self._tune, self._tune_id())

    @Property(str, notify=changed)
    def tuneTone(self) -> str:
        result = (self._tune.get("results") or {}).get(self._tune_id()) or {}
        return {"better": "ok", "failed": "error"}.get(result.get("status", ""), "")

    @Property(bool, notify=changed)
    def tuneBusy(self) -> bool:
        job = self._tune.get("job") or {}
        return job.get("status", "idle") != "idle" or bool(job.get("queue"))

    @Property(bool, notify=changed)
    def tuneAuto(self) -> bool:
        return self._tune.get("auto", True) is not False

    # --- guessing ahead (42.6) ---

    @Property(str, notify=changed)
    def specText(self) -> str:
        return spec_text(self._tune, self._tune_id(), self._launch, self._drafting)[0]

    @Property(str, notify=changed)
    def specTone(self) -> str:
        return spec_text(self._tune, self._tune_id(), self._launch, self._drafting)[1]

    @Property(bool, notify=changed)
    def specOn(self) -> bool:
        """The model's command guesses ahead: «Снять ускорение» shows."""
        return bool((self._launch.get("spec") or {}).get("type"))

    @Property(bool, notify=changed)
    def speedBusy(self) -> bool:
        """Its draft model on its way."""
        return self._drafting.get("model_id") == self._tune_id() and bool(self._drafting.get("total"))

    def _set_drafting(self, drafting: dict[str, Any] | None) -> None:
        self._drafting = drafting or {}
        self.changed.emit()

    def _load_drafting(self) -> None:
        if self.app.client is not None:
            self.app._call(lambda: self.app.client.speedup_state(), lambda s: self._set_drafting(s.get("drafting")),
                           lambda _e: None)  # fmt: skip

    @Slot()
    def speedUp(self) -> None:
        """«Ускорить»: the hub downloads the draft model paired with this one, if any, then measures at once."""
        model_id = self._tune_id()
        if model_id and self.app.client is not None:
            self.app.call_or_fail(lambda: self.app.client.speed_up(model_id),
                                  lambda answer: self._set_drafting(answer.get("drafting")),
                                  lambda e: self.app.show_error(tr("Ускорить: {error}", error=e)))  # fmt: skip

    @Slot()
    def speedOff(self) -> None:
        """«Снять ускорение»: guessing ahead off the model's command (its draft's file stays while another uses it)."""
        model_id, hub_id = self._tune_id(), self._model
        if model_id and self.app.client is not None:
            self.app.call_or_fail(lambda: self.app.client.change_llama_model(model_id, {"spec_type": "none"}),
                                  lambda _answer: self._load_launch(hub_id),
                                  lambda e: self.app.show_error(tr("Снять ускорение: {error}", error=e)))  # fmt: skip

    def _set_tune(self, state: dict[str, Any]) -> None:
        first = not self._tune
        self._tune = state
        for model_id, result in (state.get("results") or {}).items():
            key = f"{model_id} {result.get('at', '')}"
            if key in self._told:
                continue
            self._told.add(key)
            if not first and result.get("status") == "better":
                before, after = speed(result.get("before"), "tg"), speed(result.get("after"), "tg")
                self.app.flash(tr("«{model}» подобрана по замерам: ответ {before} → {after} ток/с", model=model_id,
                                  before=before, after=after))  # fmt: skip
        self.changed.emit()

    def _load_tune(self) -> None:
        if self.app.client is not None:
            self.app._call(lambda: self.app.client.tune_state(), self._set_tune, lambda _e: None)

    def _ask_tune(self, call) -> None:
        if self.app.client is not None:
            self.app.call_or_fail(call, self._set_tune,
                                  lambda e: self.app.show_error(tr("Подбор по замерам: {error}", error=e)))  # fmt: skip

    @Slot()
    def tune(self) -> None:
        model_id = self._tune_id()
        if model_id:
            self._ask_tune(lambda: self.app.client.tune(model_id))

    @Slot()
    def tuneAll(self) -> None:
        self._ask_tune(lambda: self.app.client.tune_all())

    @Slot()
    def tuneCancel(self) -> None:
        self._ask_tune(lambda: self.app.client.tune_cancel())

    @Slot(bool)
    def setTuneAuto(self, on: bool) -> None:
        self._ask_tune(lambda: self.app.client.set_tune_auto(on))

    @Property(list, constant=True)
    def presets(self) -> list:
        return [{"id": i, "name": tr(n)} for i, n in PRESETS]

    @Property(list, constant=True)
    def efforts(self) -> list:
        return [{"id": "", "name": tr("как в чате")}] + [{"id": v, "name": label} for v, label in presenter.efforts()]

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
    def checkFailed(self) -> bool:
        return self._check_failed

    @Property(bool, notify=changed)
    def checking(self) -> bool:
        return self._checking

    @Property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading

    @Property(str, notify=changed)
    def contextPlace(self) -> str:
        """Where this model's context is set (stage 40: the line under the input leads there): «launch» - llama-swap's
        `-c` of a local model, «capabilities» - the window the hub takes the provider's for; '' - not known yet."""
        if self._loading or self._launch_asked:
            return ""
        return "launch" if self._launch else "capabilities"

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
        self._model, self._check, self._check_failed, self._loading = wanted, "", False, True
        hub_id = self._hub_id()
        self._load_launch(wanted)
        self._load_tune()
        self._load_drafting()
        self.changed.emit()

        def show(info: dict[str, Any]) -> None:
            if self._hub_id() == hub_id:
                self._show(info)

        def failed(error: Exception) -> None:
            self._loading = False
            self._error = tr("Настройки не прочитаны: {error}", error=error)
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
            self.app.flash(tr("Сохранено: «{name}», версия {version}", name=self.title, version=info.get("version")))
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
            self.app.flash(tr("Возвращена версия {old} — теперь это версия {new}", old=number,
                              new=info.get("version")))  # fmt: skip
            self.app.load_models()

        self.app.call_or_fail(lambda: self.app.client.revert_model_settings(hub_id, number), done,
                              lambda e: self.app.show_error(tr("Не возвращена: {error}", error=e)))  # fmt: skip

    @Slot()
    def check(self) -> None:
        if self._model == GENERAL or self._checking:
            return
        model_id = self._model
        self._checking, self._check_failed = True, False
        self._check = (tr("Спрашиваю модель… Локальная может загружаться минуту-другую.") if self.local
                       else tr("Спрашиваю модель…"))  # fmt: skip
        self.changed.emit()

        def done(result: dict[str, Any]) -> None:
            self._checking = False
            if self._model == model_id:
                self._check, self._check_failed = check_text(result), not result.get("ok")
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._checking = False
            self._check, self._check_failed = tr("Не проверено: {error}", error=error), True
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.check_model(model_id), done, failed)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "install.draft":  # the draft model on its way (42.5)
            self._set_drafting(payload.get("drafting"))
            return
        if kind == "tune.changed":
            job = payload.get("job") or {}
            if job.get("status") == "measuring" and self._tune:  # how far the measuring got: no call for it
                self._tune = {**self._tune, "job": job}
                self.changed.emit()
            else:
                self._load_tune()
            return
        if kind == "llama.config" and not self._launch_busy and not self.launchDirty:
            self._load_launch(self._model)  # changed or restored elsewhere: show the file as it is now
        if kind == "model.settings":
            self.app.load_models()
            mine = (payload.get("model_id") or "") in (self._hub_id(), "*")
            if mine and not self.dirty and payload.get("version") != self.version and not self._loading:
                self.open(self._model)  # changed elsewhere (another window): show it, unless being edited here
