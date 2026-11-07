"""Plain words for people without technical knowledge (stage 10.6, D37): the usual errors explained, «Проверить
систему» line by line, and the logs for support - without keys or tokens in them."""

import asyncio
import types
import zipfile
from datetime import datetime

import httpx

from common import hardware
from common.paths import DataPaths
from common.plain_errors import explain
from hub import autostart, diagnostics
from hub import llama_config as lc
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def test_the_usual_errors_in_plain_words():
    cases = {
        "llama-swap: HTTP 500: CUDA error: out of memory": "Не хватает памяти",
        "llama-swap: ggml_vulkan: ErrorOutOfDeviceMemory": "Не хватает памяти",
        "llama-swap: cannot list models (All connection attempts failed)": "Локальные модели не запущены",
        "llama-swap: 502 upstream command exited prematurely": "Модель не запустилась",
        "llama-swap: failed to start command 'C:/x/llama-server.exe --port 10001': fork/exec C:/x/llama-server.exe: "
        "The system cannot find the path specified.": "Программа модели не найдена",
        "anthropic: 401 invalid x-api-key": "Ключ не подошёл",
        "openai: 429 Rate limit reached for requests": "Поставщик просит подождать",
        "openrouter: 402 insufficient credits": "Закончились деньги на счёте",
        "deepseek: All connection attempts failed": "Нет связи с интернетом",
        "gemini: The input token count exceeds the maximum context": "Разговор не помещается в модель",
        "llama-swap: HTTP 400: request (15961 tokens) exceeds the available context size (8192 tokens)":
            "Разговор не помещается в модель",
        "no answer in 5 minutes": "Модель отвечает слишком долго",
    }  # fmt: skip
    for text, title in cases.items():
        found = explain(text)
        assert found and found[0] == title, (text, found)
        assert found[1].endswith(".")
    assert explain("модель ответила странно") is None and explain("") is None


def test_the_system_check_says_what_to_do(tmp_path, monkeypatch):
    server = tmp_path / "llama.cpp" / "llama-server.exe"
    server.parent.mkdir()
    server.write_bytes(b"MZ")
    (server.parent / "ggml-cpu-x64.dll").write_bytes(b"MZ")  # a build for the processor only
    config = tmp_path / "config.yaml"
    config.write_text(f"""macros:
  llama_server: '"{server.as_posix()}"'
models:
  "a":
    cmd: |
      ${{llama_server}} -m "F:/models/a.gguf"
      --port ${{PORT}}
""", encoding="utf-8")  # fmt: skip
    monkeypatch.setenv(lc.CONFIG_ENV, str(config))
    monkeypatch.setattr(hardware, "adapters", lambda: [{"name": "RTX 4060", "vendor": "nvidia", "vram_gib": 8.0}])
    monkeypatch.setattr(hardware, "dxgi_adapters", lambda: [{"name": "RTX 4060", "vendor": "nvidia",
                                                             "dedicated_gib": 8.0, "shared_gib": 7.9}])  # fmt: skip
    monkeypatch.setattr(hardware, "vulkan_loader", lambda: True)
    monkeypatch.setattr(hardware, "nvidia_driver", lambda: {"version": "610.88", "cuda": 13030, "compute": [8, 9]})
    monkeypatch.setattr(diagnostics, "memory_sample", lambda: {"ram_total_gib": 16.0, "ram_used_gib": 9.5,
                                                               "commit_limit_gib": 20.0})  # fmt: skip
    monkeypatch.setattr(diagnostics, "drives", lambda: [{"letter": "F", "kind": "hdd", "words": "жёсткий диск",
                                                         "free_gib": 4.0}])  # fmt: skip

    async def silent(url, transport=None):
        return False

    monkeypatch.setattr(autostart, "answers", silent)
    monkeypatch.setattr(autostart, "port_taken", lambda url: None)  # whoever holds this computer's 8080
    monkeypatch.setattr(httpx.AsyncClient, "head", lambda self, url: _refused())
    monkeypatch.setattr(diagnostics.search, "ripgrep", lambda: None)
    state = types.SimpleNamespace(settings=types.SimpleNamespace(paths=DataPaths(tmp_path / "data").ensure(),
                                                                 llama_swap_url="http://127.0.0.1:8080"),
                                  ripgrep=types.SimpleNamespace(error="нет связи с GitHub (ConnectError)"))  # fmt: skip
    lines = {line["id"]: line for line in asyncio.run(diagnostics.check(state))}
    assert lines["ripgrep"]["ok"] is None and "обходом файлов" in lines["ripgrep"]["text"]
    assert lines["ripgrep"]["advice"].endswith("когда будет связь (сейчас: нет связи с GitHub (ConnectError)).")
    assert lines["hub"]["ok"] and lines["llama-swap"]["ok"] is False and "Запуск" in lines["llama-swap"]["advice"]
    assert lines["config"]["ok"] and "моделей — 1" in lines["config"]["text"]
    assert lines["gpu"]["ok"] is None and "сборка для процессора" in lines["gpu"]["advice"]
    assert "CUDA 13.3" in lines["gpu"]["text"] and "RTX 4060 (8 ГБ)" in lines["gpu"]["text"]
    assert lines["memory"]["ok"] is None and "файл подкачки ≈ 4 ГБ" in lines["memory"]["text"]
    # pictures and videos (29.2): what this computer can make, before the engine is there
    assert lines["pictures"]["ok"] is True
    assert lines["pictures"]["text"] == "RTX 4060: картинки 1024 × 1024 за секунды; видео — здесь, минуты на ролик"
    assert (
        lines["disk-F"]["ok"] is None
        and "SSD" in lines["disk-F"]["advice"]
        and "мало места" in lines["disk-F"]["advice"]
    )
    assert lines["files"]["ok"] is False and "F:/models/a.gguf" in lines["files"]["text"].replace("\\", "/")
    assert lines["net-hf"]["ok"] is False and lines["net-github"]["ok"] is False

    paths = state.settings.paths
    (paths.logs / "hub.log").write_text("ERROR key sk-ant-api03-" + "x" * 40 + " refused\n", encoding="utf-8")
    report = diagnostics.report(paths, list(lines.values()))
    with zipfile.ZipFile(report) as zf:
        assert sorted(zf.namelist()) == ["check.json", "llama-swap/config.yaml", "logs/hub.log", "versions.json"]
        log = zf.read("logs/hub.log").decode("utf-8")
        assert "sk-ant-api03-xxxx" not in log and "refused" in log  # the key is masked, the rest stays
    for _ in range(6):
        diagnostics.report(paths, [])
    assert len(list((paths.root / "reports").glob("*.zip"))) <= diagnostics.REPORT_KEEP


async def _refused():
    raise httpx.ConnectError("offline")


def test_the_check_names_a_crash_of_the_last_days_and_not_a_caught_exception(tmp_path):
    """23.2: the window's and the hub's crashes - those the next start found (common/crashes.py)."""
    logs = tmp_path / "logs"
    logs.mkdir()
    now = datetime(2026, 11, 6, 12, 0)
    line = diagnostics.crashes_line(logs, now)
    assert line["id"] == "crashes" and line["ok"] is True and "7" in line["text"]
    (logs / "app-crash.log").write_text(
        "=== 2026-10-29 09:38:11 pid 7 started\nWindows fatal exception: code 0x800706be\n\nThread ...\n"
        "=== 2026-10-29 10:14:12 pid 7 ended normally: the record above is an exception Windows caught, not a crash\n"
        "=== 2026-10-30 08:00:00 pid 8 started\nWindows fatal exception: access violation\n"
        "=== crashed at 2026-10-30 09:15:00: Windows fatal exception: access violation\n"
        "=== 2026-11-05 10:00:00 pid 9 started\nWindows fatal exception: access violation\n"
        "=== crashed at 2026-11-05 21:40:00: Windows fatal exception: access violation\n",
        encoding="utf-8")  # fmt: skip
    (logs / "hub-crash.log").write_text(
        "=== 2026-11-06 08:00:00 pid 10 started\nFatal Python error: Segmentation fault\n"
        "=== crashed at 2026-11-06 08:30:00: Fatal Python error: Segmentation fault\n", encoding="utf-8")  # fmt: skip
    line = diagnostics.crashes_line(logs, now)
    assert line["ok"] is None
    assert line["text"] == ("окно — 05.11 21:40: Windows fatal exception: access violation; "
                            "хаб — 06.11 08:30: Fatal Python error: Segmentation fault")  # fmt: skip
    assert "Собрать журналы для поддержки" in line["advice"]


def test_the_check_says_who_holds_llama_swaps_port():
    """23.4: llama-swap cannot start - a model of Strata holds 8080; the check names the program and its file."""
    taken = {"port": 8080, "pid": 4242, "name": "strata.exe", "exe": "C:\\Strata\\engine\\strata.exe"}
    line = diagnostics.llama_swap_line("http://127.0.0.1:8080", False, taken)
    assert line["id"] == "llama-swap" and line["ok"] is False
    assert line["text"] == "порт 8080 занят: strata.exe (pid 4242, C:\\Strata\\engine\\strata.exe)"
    assert "Закройте strata.exe" in line["advice"] and "сам" in line["advice"]
    assert diagnostics.llama_swap_line("http://127.0.0.1:8080", True, None)["ok"] is True


def test_the_check_and_the_report_through_the_api(hub):  # noqa: F811
    from hub.auth import create_client

    lines = {line["id"]: line for line in hub.get("/v1/support/check").json()["lines"]}
    assert lines["hub"]["ok"] and lines["net-hf"]["ok"] is False  # the tests' stand-in address: nobody there
    made = hub.post("/v1/support/report").json()
    assert made["path"].endswith(".zip") and {line["id"] for line in made["lines"]} == set(lines)
    other = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.get("/v1/support/check", headers={"Authorization": f"Bearer {other}"}).status_code == 403
