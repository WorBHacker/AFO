"""The OpenAI-compatible adapter: model listing, SSE parsing, loading/reasoning/content split, cost."""

import asyncio
import json

import httpx
import pytest

from hub.gateway.base import cost_usd
from hub.gateway.openai_compat import OpenAICompatProvider
from hub.gateway.registry import locality_of


def sse(*chunks: str) -> bytes:
    return "".join(f"data: {c}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


async def collect(provider, cancel=None):
    cancel = cancel or asyncio.Event()
    return [e async for e in provider.stream_chat("coder", [{"role": "user", "content": "hi"}], "medium", cancel)]


def provider_with(handler) -> OpenAICompatProvider:
    return OpenAICompatProvider("llama-swap", "http://x", transport=httpx.MockTransport(handler))


def test_list_models_maps_fields():
    def handler(request):
        return httpx.Response(
            200,
            json={"data": [{"id": "coder", "name": "Coder", "meta": {"vision": True, "context": 65536}}]},
        )

    models = asyncio.run(provider_with(handler).list_models())
    assert len(models) == 1
    m = models[0]
    assert (m.id, m.provider, m.model_ref, m.locality) == ("llama-swap/coder", "llama-swap", "coder", "local")
    assert m.capabilities["vision"] is True
    assert m.prices == {}


def test_list_models_wraps_errors():
    def handler(request):
        return httpx.Response(503, text="down")

    from hub.gateway.base import ProviderError

    with pytest.raises(ProviderError):
        asyncio.run(provider_with(handler).list_models())


def test_stream_splits_reasoning_and_content_and_ends_with_usage():
    body = sse(
        '{"choices":[{"delta":{"reasoning_content":"think"}}]}',
        '{"choices":[{"delta":{"content":"Hel"}}]}',
        '{"choices":[{"delta":{"content":"lo"}}]}',
        '{"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"prompt_tokens_details":{"cached_tokens":4}}}',
    )

    def handler(request):
        return httpx.Response(200, content=body)

    events = asyncio.run(collect(provider_with(handler)))
    kinds = [(e.kind, e.text) for e in events if e.kind != "end"]
    assert kinds == [("reasoning", "think"), ("content", "Hel"), ("content", "lo")]
    end = events[-1]
    assert end.kind == "end"
    assert end.usage == {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2}


def test_loading_lines_are_separated_from_reasoning():
    body = sse(
        '{"choices":[{"delta":{"reasoning_content":"llama-swap loading model: coder"}}]}',
        '{"choices":[{"delta":{"reasoning_content":"Done! (12s)"}}]}',
        '{"choices":[{"delta":{"content":"ready"}}]}',
        '{"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
    )

    def handler(request):
        return httpx.Response(200, content=body)

    events = asyncio.run(collect(provider_with(handler)))
    assert [e.kind for e in events if e.kind == "loading"]  # loading was reported
    assert all(e.kind != "reasoning" for e in events)  # ...and not as reasoning
    assert [e.text for e in events if e.kind == "content"] == ["ready"]


def test_the_loading_banner_in_pieces():
    """llama-swap v267+ (found live 2026-10-28): the banner in pieces of five characters between lines of ━; none of
    it is reasoning, the model's own reasoning after it is; a stream that only looks like the banner's start is not
    lost; the reasoning of a model already loaded starts at once."""
    banner = ["━━━━━", "\n", "llama", "-swap", " load", "ing m", "odel:", " code", "r", "\n", "\n", "Done!", " (0.4",
              "7s)", "\n", "━━━━━", "\n", " ", "\n"]  # fmt: skip
    lines = [json.dumps({"choices": [{"delta": {"reasoning_content": piece}}]}) for piece in banner]
    body = sse(*lines, '{"choices":[{"delta":{"reasoning_content":"I think"}}]}',
               '{"choices":[{"delta":{"content":"ready"}}]}')  # fmt: skip
    events = asyncio.run(collect(provider_with(lambda request: httpx.Response(200, content=body))))
    assert "".join(e.text for e in events if e.kind == "loading") == "".join(banner)
    assert [e.text for e in events if e.kind == "reasoning"] == ["I think"]
    assert [e.text for e in events if e.kind == "content"] == ["ready"]

    for pieces, kinds in (([" ━", "ll"], [("reasoning", " ━ll")]),  # held to the end: let through
                          (["let me", " see"], [("reasoning", "let me"), ("reasoning", " see")])):  # fmt: skip
        body = sse(*[json.dumps({"choices": [{"delta": {"reasoning_content": p}}]}) for p in pieces])
        events = asyncio.run(collect(provider_with(lambda request, body=body: httpx.Response(200, content=body))))
        assert [(e.kind, e.text) for e in events if e.kind != "end"] == kinds


def test_http_error_becomes_provider_error():
    from hub.gateway.base import ProviderError

    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(ProviderError, match="500"):
        asyncio.run(collect(provider_with(handler)))


def test_cancel_stops_the_stream_but_still_ends():
    body = sse(*[f'{{"choices":[{{"delta":{{"content":"{i}"}}}}]}}' for i in range(100)])

    def handler(request):
        return httpx.Response(200, content=body)

    cancel = asyncio.Event()
    cancel.set()  # cancelled before the first line is read
    events = asyncio.run(collect(provider_with(handler), cancel))
    assert events[-1].kind == "end"
    assert not [e for e in events if e.kind == "content"]


@pytest.mark.parametrize(
    ("usage", "prices", "expected"),
    [
        ({"input_tokens": 1_000_000, "output_tokens": 1_000_000}, {}, 0.0),  # local model: free
        ({"input_tokens": 1_000_000, "output_tokens": 1_000_000}, {"input": 2.0, "output": 10.0}, 12.0),
        (
            {"input_tokens": 1_000_000, "cached_input_tokens": 400_000, "output_tokens": 0},
            {"input": 2.0, "cache_read": 0.2},
            600_000 / 1e6 * 2.0 + 400_000 / 1e6 * 0.2,
        ),
    ],
)
def test_cost(usage, prices, expected):
    assert cost_usd(usage, prices) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("url", "locality"),
    [("http://127.0.0.1:8080", "local"), ("http://localhost:8080/v1", "local"), ("http://[::1]:8080", "local"),
     ("http://192.168.1.20:8080", "cloud"), ("https://gpu.example.com", "cloud"), ("http://box:8080", "cloud")],
)  # fmt: skip
def test_a_llama_swap_elsewhere_counts_as_the_cloud(url, locality):
    """The audit (D46): what a chat sends to llama-swap on another machine leaves this one - D10 treats it so."""
    assert locality_of(url) == locality
