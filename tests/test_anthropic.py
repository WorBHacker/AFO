"""The Anthropic adapter against a fake SDK client: catalog, request shape, replay of Claude's own blocks,
refusals, cancellation and error mapping. No network, no real key."""

import asyncio
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from hub.gateway.anthropic_provider import AnthropicProvider, replay_blocks, to_wire
from hub.gateway.base import ProviderError, cost_usd

SIGNED = {"type": "thinking", "thinking": "кратко: поздороваться", "signature": "sig-1"}


def message(content, *, stop_reason="end_turn", usage=None, stop_details=None) -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_details": stop_details,
            "usage": usage
            or {
                "input_tokens": 1000,
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 500,
                "output_tokens": 100,
            },
        }
    )


class FakeStream:
    def __init__(self, events, snapshot, cancel_after=None, cancel=None):
        self.events = events
        self.snapshot = snapshot
        self.cancel_after = cancel_after
        self.cancel = cancel
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def __aiter__(self):
        for i, event in enumerate(self.events):
            if self.cancel_after is not None and i == self.cancel_after:
                self.cancel.set()
            yield event

    @property
    def current_message_snapshot(self):
        return self.snapshot


class FakeClient:
    def __init__(self, stream=None, models=(), list_error=None, stream_error=None):
        self.requests = []
        self._stream = stream
        self._models = models
        self._list_error = list_error
        self._stream_error = stream_error
        self.messages = SimpleNamespace(stream=self._open_stream)
        self.models = SimpleNamespace(list=self._list)

    def _open_stream(self, **request):
        self.requests.append(request)
        if self._stream_error:
            raise self._stream_error
        return self._stream

    async def _list(self):
        if self._list_error:
            raise self._list_error
        for model in self._models:
            yield model


def provider(client, key="sk-test") -> AnthropicProvider:
    made = []

    def factory(api_key):
        made.append(api_key)
        return client

    p = AnthropicProvider(lambda: key, client_factory=factory)
    p.made = made
    return p


def default_events():
    return [
        SimpleNamespace(type="message_start"),
        SimpleNamespace(type="thinking", thinking="кратко: "),
        SimpleNamespace(type="thinking", thinking="поздороваться"),
        SimpleNamespace(type="signature"),
        SimpleNamespace(type="text", text="При"),
        SimpleNamespace(type="text", text="вет"),
        SimpleNamespace(type="message_stop"),
    ]


async def collect(p, messages, cancel=None, effort="high"):
    cancel = cancel or asyncio.Event()
    return [e async for e in p.stream_chat("claude-opus-5-5", messages, effort, cancel)]


def http_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


# --- catalog ---


def test_no_key_means_no_models_and_no_network():
    client = FakeClient()
    p = provider(client, key=None)
    assert asyncio.run(p.list_models()) == []
    assert p.made == []  # no SDK client was even created


def test_catalog_is_the_priced_models_the_key_can_use():
    def info(ref, name, context=1_000_000):
        return SimpleNamespace(id=ref, display_name=name, max_input_tokens=context, max_tokens=128_000)

    listed = [
        info("claude-opus-5-5", "Claude Opus 5.5"),
        info("claude-haiku-5-5", "Claude Haiku 5.5"),
        info("claude-unknown-9", "Unpriced", context=None),
    ]
    models = asyncio.run(provider(FakeClient(models=listed)).list_models())
    assert [m.id for m in models] == ["anthropic/claude-opus-5-5", "anthropic/claude-haiku-5-5"]
    opus = models[0]
    assert (opus.provider, opus.model_ref, opus.locality, opus.name) == (
        "anthropic",
        "claude-opus-5-5",
        "cloud",
        "Claude Opus 5.5",
    )
    assert opus.prices["input"] == 4.0 and opus.prices["output"] == 20.0
    assert opus.capabilities["context"] == 1_000_000


def test_rejected_key_is_a_clean_provider_error():
    client = FakeClient(list_error=http_error(anthropic.AuthenticationError, 401))
    with pytest.raises(ProviderError, match="key was rejected"):
        asyncio.run(provider(client).list_models())


# --- one request ---


def test_request_shape_events_usage_and_raw():
    snapshot = message([SIGNED, {"type": "text", "text": "Привет"}])
    client = FakeClient(stream=FakeStream(default_events(), snapshot))
    events = asyncio.run(
        collect(provider(client), [{"role": "system", "content": "будь краток"}, {"role": "user", "content": "hi"}])
    )

    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["system"] == "будь краток"
    assert request["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert request["output_config"] == {"effort": "high"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert not {"temperature", "top_p", "top_k"} & set(request)  # a 400 on current models (D15)
    assert request["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]

    assert [(e.kind, e.text) for e in events[:-1]] == [
        ("reasoning", "кратко: "),
        ("reasoning", "поздороваться"),
        ("content", "При"),
        ("content", "вет"),
    ]
    end = events[-1]
    assert end.kind == "end"
    assert end.usage == {
        "input_tokens": 3500,  # fresh + cache read + cache write
        "cached_input_tokens": 2000,
        "cache_write_tokens": 500,
        "output_tokens": 100,
    }
    assert end.stop_reason == "end_turn"
    assert end.refusal is None
    assert end.raw["content"][0] == SIGNED  # stored as received, signature included
    assert client._stream.closed


def test_cost_of_a_cached_claude_request():
    usage = {"input_tokens": 3500, "cached_input_tokens": 2000, "cache_write_tokens": 500, "output_tokens": 100}
    prices = {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0}
    expected = (1000 * 4.0 + 2000 * 0.20 + 500 * 5.0 + 100 * 20.0) / 1e6
    assert cost_usd(usage, prices) == pytest.approx(expected)


def test_refusal_ends_with_its_usage_instead_of_raising():
    snapshot = message(
        [{"type": "text", "text": "Нача"}],
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": None},
    )
    client = FakeClient(stream=FakeStream([SimpleNamespace(type="message_start")], snapshot))
    end = asyncio.run(collect(provider(client), [{"role": "user", "content": "x"}]))[-1]
    assert end.stop_reason == "refusal"
    assert "declined" in end.refusal and "cyber" in end.refusal
    assert end.usage["output_tokens"] == 100  # spent tokens are still reported


def test_cancel_stops_and_estimates_output():
    partial = message(
        [{"type": "thinking", "thinking": "кратко: ", "signature": ""}],
        usage={"input_tokens": 50, "output_tokens": 1},
    )
    cancel = asyncio.Event()
    stream = FakeStream(default_events(), partial, cancel_after=2, cancel=cancel)
    events = asyncio.run(collect(provider(FakeClient(stream=stream)), [{"role": "user", "content": "x"}], cancel))
    assert [e.kind for e in events] == ["reasoning", "end"]  # stopped right after the cancel
    end = events[-1]
    assert end.usage["estimated"] == 1
    assert end.usage["output_tokens"] >= 1
    assert stream.closed  # leaving the context closes the HTTP stream, so Claude stops generating


def test_no_key_and_sdk_errors_become_provider_errors():
    with pytest.raises(ProviderError, match="no Anthropic API key"):
        asyncio.run(collect(provider(FakeClient(), key=""), [{"role": "user", "content": "x"}]))
    client = FakeClient(stream_error=http_error(anthropic.RateLimitError, 429))
    with pytest.raises(ProviderError, match="429"):
        asyncio.run(collect(provider(client), [{"role": "user", "content": "x"}]))


def test_one_sdk_client_per_key():
    client = FakeClient(models=[])
    keys = iter(["k1", "k1", "k2"])
    made = []
    p = AnthropicProvider(lambda: next(keys), client_factory=lambda k: made.append(k) or client)
    for _ in range(3):
        asyncio.run(p.list_models())
    assert made == ["k1", "k2"]


# --- replaying the history (D14) ---


def test_claude_turns_are_replayed_from_raw_others_as_text():
    raw = {"content": [SIGNED, {"type": "text", "text": "Привет", "citations": None}]}
    history = [
        {"role": "user", "content": "раз"},
        {"role": "assistant", "content": "Привет", "provider": "anthropic", "provider_raw": raw},
        {"role": "user", "content": "два"},
        {"role": "assistant", "content": "локальный ответ", "provider": "llama-swap", "provider_raw": {"x": 1}},
        {"role": "user", "content": "три"},
    ]
    system, turns = to_wire(history, "anthropic")
    assert system == ""
    assert turns[1] == {"role": "assistant", "content": [SIGNED, {"type": "text", "text": "Привет"}]}
    assert turns[3] == {"role": "assistant", "content": [{"type": "text", "text": "локальный ответ"}]}
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant", "user"]


def test_adjacent_user_turns_are_joined_deterministically():
    history = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    first = to_wire(history, "anthropic")
    assert first[1] == [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]
    assert to_wire(history, "anthropic") == first  # same history, same bytes: signatures and cache stay valid


def test_replay_drops_only_what_cannot_be_sent():
    content = [
        {"type": "thinking", "thinking": "оборвано", "signature": ""},  # cancelled before the signature
        {"type": "redacted_thinking", "data": "opaque"},
        SIGNED,
        {"type": "text", "text": ""},
        {"type": "text", "text": "ответ"},
    ]
    assert replay_blocks(content) == [
        {"type": "redacted_thinking", "data": "opaque"},
        SIGNED,
        {"type": "text", "text": "ответ"},
    ]


def test_the_sdk_comes_with_the_first_call_not_with_the_hub():
    """Importing the SDK took ~1.8 s - two thirds of the hub's start (D46, step 13.7): the hub and its gateway are
    built without it, and a hub with no Anthropic key never loads it."""
    import subprocess
    import sys

    from common.paths import PROJECT_ROOT

    code = (
        "import asyncio, sys\n"
        "import hub.server\n"
        "from hub.gateway.anthropic_provider import AnthropicProvider\n"
        "print(asyncio.run(AnthropicProvider(lambda: None).list_models()), 'anthropic' in sys.modules)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["[]", "False"]
