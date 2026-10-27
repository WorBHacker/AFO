"""Starting by itself (Q8): the hub at Windows login (a Run-key value, here in memory - the registry is never touched)
and llama-swap started by the hub when it does not answer (here a fake starter and a mock llama-swap)."""

import asyncio

import httpx

from hub import autostart
from hub.auth import create_client
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def test_the_login_value():
    store = autostart.MemoryRunStore()
    assert not autostart.login_enabled(store)
    autostart.set_login(store, True)
    assert store.value == autostart.login_command() and store.value.endswith('" -m hub')
    assert "pythonw.exe" in store.value.lower() or "python" in store.value.lower()
    autostart.set_login(store, False)
    assert store.value is None and isinstance(autostart.default_run_store(), autostart.MemoryRunStore)


class FakeLlamaSwap:
    """Answers once "started"; counts the starts."""

    def __init__(self, up=False):
        self.up = up
        self.starts = []

    def transport(self):
        def answer(request):
            if not self.up:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, json={"data": []})

        return httpx.MockTransport(answer)

    def starter(self, script, log_file):
        self.starts.append((script, log_file))
        self.up = True


def ensure(fake, url="http://127.0.0.1:8080", wanted=True, script=None, tmp_path=None):
    script = script or (tmp_path / "start-llama-swap.cmd")
    return asyncio.run(autostart.ensure_llama_swap(url, script, wanted, tmp_path / "logs" / "out.log",
                                                   starter=fake.starter, transport=fake.transport(),
                                                   ready_timeout=3))  # fmt: skip


def test_llama_swap_is_started_only_when_it_is_down_and_wanted(tmp_path):
    script = tmp_path / "start-llama-swap.cmd"
    assert ensure(FakeLlamaSwap(up=True), tmp_path=tmp_path) == "running"
    down = FakeLlamaSwap()
    assert ensure(down, tmp_path=tmp_path) == "missing" and down.starts == []  # no script: nothing to run
    script.write_text("@echo off\n", encoding="utf-8")
    assert ensure(down, wanted=False, tmp_path=tmp_path) == "off" and down.starts == []
    assert ensure(down, tmp_path=tmp_path) == "started" and down.starts == [(script, tmp_path / "logs" / "out.log")]
    remote = FakeLlamaSwap()
    assert ensure(remote, url="http://192.168.0.5:8080", tmp_path=tmp_path) == "remote" and remote.starts == []

    class Stuck(FakeLlamaSwap):
        def starter(self, script, log_file):
            self.starts.append(script)  # started, but never answers within the wait

    assert ensure(Stuck(), tmp_path=tmp_path) == "slow"


def test_the_api(hub):  # noqa: F811
    found = hub.get("/v1/autostart").json()
    assert found["login"] is False and found["llama_swap"] is True and found["login_command"].endswith("-m hub")
    changed = hub.put("/v1/autostart", json={"login": True, "llama_swap": False}).json()
    assert changed["login"] is True and changed["llama_swap"] is False
    assert hub.app.state.run_store.get() == found["login_command"]
    assert hub.put("/v1/autostart", json={"login": False}).json() == {**changed, "login": False}
    feed = hub.get("/v1/feed", params={"types": "autostart.changed"}).json()["events"]
    assert len(feed) == 2
    other = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.put("/v1/autostart", json={"login": True}, headers={"Authorization": f"Bearer {other}"}).status_code \
        == 403  # fmt: skip


def test_the_test_hub_never_starts_llama_swap(hub):  # noqa: F811
    assert hub.app.state.settings.manage_llama_swap is False  # only `python -m hub` (HubSettings.from_env) does
