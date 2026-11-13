"""Stage 43: the tray and the graphics card for models - one window, the tray, its menu, Windows' login."""

import gc
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import autostart
from hub.server import create_app
from hub.settings import HubSettings
from tests.test_code_editor import shown
from tests.test_entry_points import free_port
from tests.test_tuning import listening
from tests.test_ui import FakeClient, qapp, wait_until  # noqa: F401 - the qapp fixture


def test_a_start_in_the_tray_shows_no_window_until_asked(qapp):  # noqa: F811
    """43.1: `--tray` (Windows' login) starts AFO without its window - the tray only; the window comes when asked (a
    second start, the tray's «Открыть AFO»), as it was left: maximized stays maximized."""
    from app.ui.main import QuickApp

    paths = data_paths().ensure()
    quick = QuickApp(qapp, paths, connect=False, hidden=True)
    try:
        assert not quick.window.isVisible()
        quick.show_window()
        assert quick.window.isVisible()
    finally:
        quick.close()


class Quitting(FakeClient):
    def __init__(self):
        super().__init__()
        self.shutdowns: list[bool] = []

    def shutdown(self, everything: bool = False):
        self.shutdowns.append(everything)


def test_the_cross_hides_the_window_in_the_tray_and_quitting_stops_everything(qapp):  # noqa: F811
    """43.2 (the user's word, 2026-11-13): the window's cross hides it in the tray - AFO goes on, its hub too; with the
    switch off (or no tray) it closes as before - the window only, the hub goes on (D2). «Выход» in the tray stops
    everything: the window, the hub, the models and llama-swap."""
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    quits: list[int] = []
    quick.quit_app = lambda: quits.append(1)
    try:
        app = quick.controller
        client = Quitting()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.set_tray(True)  # the offscreen platform has no tray: as on the desktop
        assert app.closeToTray and app.hidesOnClose  # by default
        quick.window.close()
        wait_until(lambda: not quick.window.isVisible())
        assert quits == [] and client.shutdowns == []  # hidden, not closed

        quick.show_window()
        app.setCloseToTray(False)
        assert not app.hidesOnClose and quick.settings.value("ui/close_to_tray") in (False, "false")
        quick.window.close()
        wait_until(lambda: quits == [1])
        assert client.shutdowns == []  # the window only: the hub goes on

        app.setCloseToTray(True)
        quick.show_window()
        quick.window.close()  # hidden again
        wait_until(lambda: not quick.window.isVisible())
        quick.quit_all()  # «Выход», the window hidden
        wait_until(lambda: quits == [1, 1])
        assert client.shutdowns == [True]  # everything
    finally:
        quick.close()


def test_the_trays_menu_does_what_the_window_would(qapp):  # noqa: F811
    """43.3 (the user's word, 2026-11-13): the tray's menu - «Открыть AFO», «Новый чат», «Проекты» (those whose chats
    were busy last first), the loaded model and its unloading (the card free for a game), the quiet hours on or off,
    «Остановить агента» while it acts, «Настройки», «Выход». Filled as things are when it opens; the window comes for
    what needs it."""
    from app.ui import tray
    from app.ui.main import QuickApp

    projects = [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}, {"id": "c", "name": "Gamma"}]
    chats = [{"id": "1", "home_project": "c", "updated_at": "2026-11-13T09:00:00Z"},
             {"id": "2", "home_project": "b", "updated_at": "2026-11-12T09:00:00Z"},
             {"id": "3", "home_project": "c", "updated_at": "2026-11-01T09:00:00Z"}, {"id": "4"}]  # fmt: skip
    assert [p["id"] for p in tray.recent_projects(projects, chats)] == ["c", "b", "a"]

    quick = QuickApp(qapp, data_paths().ensure(), connect=False, hidden=True)
    quits: list[int] = []
    quick.quit_app = lambda: quits.append(1)
    try:
        app = quick.controller
        client = Quitting()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.projects.projectList and app.schedules_view.quiet.get("from"))
        app.set_tray(True)  # the offscreen platform has no tray: as on the desktop (a hidden window is in the tray)
        menu = quick.tray_menu
        menu.refresh()
        assert menu.shown() == ["trayOpen", "trayNewChat", "trayProjects", "trayModel", "trayQuiet", "traySettings",
                                "trayQuit"]  # fmt: skip
        assert menu.model.text() == "Выгрузить модель: coder" and menu.model.isEnabled()
        assert menu.quiet.isChecked() and menu.quiet.text() == "Тихие часы (23:00–08:00)"

        first = menu.projects[0]
        first.trigger()
        assert app.mode == "projects" and app.projects.currentId == first.data() and quick.window.isVisible()
        quick.window.hide()

        menu.model.trigger()
        wait_until(lambda: getattr(client, "unloaded", 0) == 1)
        menu.quiet.trigger()
        wait_until(lambda: app.schedules_view.quiet.get("enabled") is False, timeout=15)  # saved, read back
        menu.refresh()
        assert not menu.quiet.isChecked()

        opened: list[str] = []
        app.uiRequested.connect(opened.append)
        menu.settings.trigger()
        assert opened == ["settings"] and quick.window.isVisible()

        quick.notifier.show_stop(True, lambda: opened.append("stopped"))
        menu.refresh()
        assert menu.stop.isVisible()
        menu.stop.trigger()
        assert opened[-1] == "stopped"

        menu.new_chat.trigger()
        assert app.mode == "chats" and app.current is None
        quick.window.hide()
        menu.quit.trigger()
        wait_until(lambda: quits == [1])
        assert client.shutdowns == [True]
    finally:
        quick.close()


SWAP = r'''"""A stand-in llama-swap: listens where --listen says; /api/models/unload is written down; it runs a
«model» (a child that sleeps), as llama-swap runs llama-server."""
import json, pathlib, subprocess, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

here = pathlib.Path(__file__).parent
args = sys.argv[1:]
host, port = args[args.index("--listen") + 1].rsplit(":", 1)
model = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
(here / "model.pid").write_text(str(model.pid))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"data": []})

    def do_POST(self):
        with open(here / "unloaded.txt", "a") as f:
            f.write(self.path + "\n")
        self._send({"msg": "ok"})


HTTPServer((host, int(port)), Handler).serve_forever()
'''


@pytest.fixture
def swap(tmp_path):
    """A llama-swap.exe that runs: the venv's launcher under that name (Windows sees llama-swap.exe, its Python runs
    the stand-in), with its --config and --listen as AFO starts it."""
    folder = tmp_path / "swap"
    (folder / "Scripts").mkdir(parents=True)
    exe = folder / "Scripts" / "llama-swap.exe"
    shutil.copy(sys.executable, exe)
    shutil.copy(Path(sys.prefix) / "pyvenv.cfg", folder / "pyvenv.cfg")
    (folder / "server.py").write_text(SWAP, encoding="utf-8")
    port = free_port()
    process = subprocess.Popen([str(exe), str(folder / "server.py"), "--config", "config.yaml", "--listen",
                                f"127.0.0.1:{port}"], stderr=subprocess.DEVNULL)  # fmt: skip
    deadline = time.monotonic() + 30
    while not (folder / "model.pid").is_file() or not listening(port):
        assert time.monotonic() < deadline, "the stand-in llama-swap did not start"
        time.sleep(0.2)
    yield folder, port, process
    for p in [*psutil.Process(process.pid).children(recursive=True), psutil.Process(process.pid)] if process.poll(
            ) is None else []:  # fmt: skip
        p.kill()


def test_quitting_from_the_tray_stops_the_models_and_llama_swap(swap):
    """43.2: «Выход» (POST /v1/shutdown with everything): llama-swap's models are unloaded; llama-swap AFO starts is
    stopped with what it runs (llama-server, Strata) - one the person runs and AFO does not start is only unloaded;
    then the hub stops."""
    folder, port, process = swap
    model = psutil.Process(int((folder / "model.pid").read_text()))
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url=f"http://127.0.0.1:{port}", status_interval_s=0))
    stops: list[int] = []
    with TestClient(app, base_url="http://127.0.0.1:8765") as hub:
        app.state.request_shutdown = lambda: stops.append(1)
        token = {"Authorization": "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")}
        app.state.db.call(lambda conn: autostart.set_llama_swap_wanted(conn, False))  # the person's own
        answer = hub.post("/v1/shutdown", json={"everything": True}, headers=token)
        assert answer.status_code == 202 and answer.json()["llama_swap"] == "unloaded" and stops == [1]
        assert (folder / "unloaded.txt").read_text().splitlines() == ["/api/models/unload"]
        assert process.poll() is None and model.is_running()

        app.state.db.call(lambda conn: autostart.set_llama_swap_wanted(conn, True))  # AFO starts it
        answer = hub.post("/v1/shutdown", json={"everything": True}, headers=token)
        assert answer.status_code == 202 and answer.json()["llama_swap"] == "stopped" and stops == [1, 1]
        assert process.wait(timeout=15) is not None
        wait_until(lambda: not model.is_running())  # what it ran went with it
        assert hub.post("/v1/shutdown", headers=token).json()["status"] == "stopping"  # the hub alone, as before


GPU_STATE = {"available": True, "on": True, "integrated": "Intel(R) Graphics", "card": "NVIDIA GeForce RTX 5080",
             "freed_mib": 126.0, "programs": [
                 {"exe": r"C:\Apps\Code.exe", "name": "Code.exe", "mib": 115.0, "where": "waits", "why": ""},
                 {"exe": r"C:\Apps\Telegram.exe", "name": "Telegram.exe", "mib": 126.0, "where": "moved", "why": ""},
                 {"exe": r"E:\Games\x.exe", "name": "x.exe", "mib": 900.0, "where": "kept", "why": "game"},
                 {"exe": r"C:\Windows\explorer.exe", "name": "explorer.exe", "mib": 466.0, "where": "kept",
                  "why": "windows"}]}  # fmt: skip


class OnTheCard(Quitting):
    def __init__(self):
        super().__init__()
        self.gpu_state = json.loads(json.dumps(GPU_STATE))
        self.gpu_calls: list[tuple] = []

    def gpu(self):
        return json.loads(json.dumps(self.gpu_state))

    def set_gpu(self, on):
        self.gpu_calls.append(("on", on))
        self.gpu_state["on"] = on
        return self.gpu()

    def gpu_choose(self, exe, where):
        self.gpu_calls.append(("choose", exe, where))
        return self.gpu()

    def gpu_give_back(self):
        self.gpu_calls.append(("give_back",))
        self.gpu_state["on"] = False
        return {"given_back": [r"C:\Apps\Telegram.exe"], "left": [], "state": self.gpu()}


def test_the_graphics_card_for_models_in_the_window_and_the_tray(qapp):  # noqa: F811
    """43.7: «Настройки → Трей и видеокарта» - the cross, AFO at sign-in, the tray menu's items; «Видеокарта —
    моделям»: the switch, each program's place and why (a moved one waits for its restart), the person's choice for one,
    «Вернуть как было» asked first. The tray: the switch with what it freed. Programs moved - a notice: restart them."""
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = OnTheCard()
        app.attach(client, start_events=False)
        app.set_tray(True)
        gpu = app.gpu_view
        wait_until(lambda: not app.bg.busy and gpu.available, timeout=10)
        assert gpu.summary == ("Встроенная: Intel(R) Graphics · видеокарта: NVIDIA GeForce RTX 5080 · переведено "
                               "программ: 2, освобождено ~126 МБ · ждут перезапуска: 1")  # fmt: skip
        rows = {r["name"]: r for r in gpu.programs}
        assert rows["Code.exe"]["text"] == "переведена — освободит видеопамять после перезапуска"
        assert rows["x.exe"]["text"] == "на видеокарте (игра)" and rows["x.exe"]["offer"] == "integrated"
        assert rows["Telegram.exe"]["offerText"] == "Оставить на видеокарте"

        menu = quick.tray_menu
        menu.refresh()
        assert "trayGpu" in menu.shown() and menu.gpu.isChecked()
        assert menu.gpu.text() == "Видеокарта — моделям (освобождено ~126 МБ)"
        menu.gpu.trigger()
        wait_until(lambda: ("on", False) in client.gpu_calls and not gpu.on)
        app.setTrayItem("model", False)
        app.setTrayItem("gpu", False)
        menu.refresh()
        assert "trayModel" not in menu.shown() and "trayGpu" not in menu.shown()
        assert quick.settings.value("tray/model") in (False, "false")

        sheet = quick.window.findChild(QObject, "settingsTray")
        app.openUi("settings-tray")
        wait_until(lambda: sheet.property("opened"))
        for name in ("closeToTray", "trayLogin", "trayItem_projects", "trayItem_gpu", "gpuOn", "gpuSummary",
                     "gpuChoose", "gpuGiveBack"):  # fmt: skip
            assert shown(quick, name) is not None, name
        gpu.choose(r"E:\Games\x.exe", "integrated")
        wait_until(lambda: ("choose", r"E:\Games\x.exe", "integrated") in client.gpu_calls)
        gpu.giveBack()
        assert app.question["title"] == "Вернуть графику программ как было?"
        app.answer(True)
        wait_until(lambda: ("give_back",) in client.gpu_calls and not gpu.on)
        QMetaObject.invokeMethod(sheet, "close")

        quick.window.hide()  # behind: the notice goes to the tray
        app.on_event({"type": "gpu.moved", "payload": {"programs": [{"exe": r"C:\Apps\Code.exe", "name": "Code.exe",
                                                                      "mib": 115.0}]}})  # fmt: skip
        assert quick.notifier.shown[-1] == (
            "Видеокарта — моделям",
            "Перезапустите их, чтобы освободить видеопамять для моделей: Code.exe",
        )
    finally:
        quick.close()


def test_a_second_starts_word_is_not_lost_while_the_first_is_busy(qapp, tmp_path):  # noqa: F811
    """43.10 (the stage's whole pytest run, 2026-11-13: the second start asked, the first never heard): the second
    start wrote its word and left at once - when the first window's thread was busy longer than about two seconds
    just then (QML being built, a hitch), the word went with the pipe, and a shortcut did nothing. Now the second waits
    for the first's «ok» before it leaves. Real processes: the second start in its own Python, this thread blocked
    while it asks."""
    from app.single import first

    single = first(tmp_path)
    assert single is not None
    words = []
    single.asked.connect(words.append)
    code = ("import sys\nfrom pathlib import Path\nfrom PySide6.QtCore import QCoreApplication\n"
            "from app.single import ask_running\napp = QCoreApplication([])\n"
            f"sys.exit(0 if ask_running(Path({str(tmp_path)!r}), 'show') else 3)\n")  # fmt: skip
    try:
        second = subprocess.Popen([sys.executable, "-c", code], cwd=Path(__file__).parent.parent)
        time.sleep(6)  # this thread busy: nothing of Qt's runs here meanwhile
        wait_until(lambda: words == ["show"], timeout=20)
        assert second.wait(timeout=30) == 0
    finally:
        single.close()
    # closed and dropped right after the word, as a restart does: the socket of the second start, its «ok» on the
    # way, went with the server - Qt then touched it after it was gone, and the process fell
    del single
    gc.collect()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
