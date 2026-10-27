"""The gateway: holds one adapter per provider, keeps the models table in sync, resolves a model id."""

from __future__ import annotations

import logging

from ..db import Database
from ..secrets import ANTHROPIC_API_KEY, SecretStore
from .base import ModelNotFound, Provider, ProviderError

log = logging.getLogger("hub.gateway")


class Gateway:
    def __init__(self, db: Database):
        self._db = db
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    @property
    def providers(self) -> list[str]:
        return sorted(self._providers)

    def provider(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def resolve(self, model_id: str) -> tuple[Provider, str]:
        """'llama-swap/coder' -> (provider, 'coder'). The provider part is the name before the first '/'."""
        provider_name, _, model_ref = model_id.partition("/")
        provider = self._providers.get(provider_name)
        if provider is None or not model_ref:
            raise ModelNotFound(f"no provider for model id {model_id!r}")
        return provider, model_ref

    async def sync_catalog(self) -> dict[str, int | str]:
        """Asks every provider for its models: those it lists are upserted and enabled, its other models are
        disabled (a removed API key, a model dropped from llama-swap). A provider that fails keeps its rows as
        they were, and does not stop the rest."""
        from ..store import disable_models_except, upsert_model

        total = 0
        errors: dict[str, str] = {}
        for name, provider in list(self._providers.items()):  # one may be registered while this awaits
            try:
                models = await provider.list_models()
            except ProviderError as exc:
                errors[name] = exc.detail
                log.warning("catalog sync: %s failed: %s", name, exc.detail)
                continue

            def write(conn, name=name, models=models):
                for model in models:
                    upsert_model(conn, model)
                disable_models_except(conn, name, [model.id for model in models])

            await self._db.acall(write)
            total += len(models)
        result: dict[str, int | str] = {"synced": total, "providers": len(self._providers)}
        if errors:
            result["errors"] = "; ".join(f"{k}: {v}" for k, v in errors.items())
        return result


def build_default_gateway(db: Database, llama_swap_url: str, secrets: SecretStore) -> Gateway:
    """The gateway: local models through llama-swap, Claude by API key (D34), Gemini, OpenAI, DeepSeek and OpenRouter
    by API key (D6), and Claude Code when its CLI is installed (stage 4, D8) - the chat runs those through
    hub/claude_code.py. A provider without its key offers no models and makes no network call."""
    from ..claude_code import ClaudeCodeProvider
    from ..connections import DEEPSEEK_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY, chosen_models
    from .anthropic_provider import AnthropicProvider
    from .cloud_openai import DeepSeekProvider, OpenRouterProvider
    from .gemini_provider import GeminiProvider
    from .openai_compat import OpenAICompatProvider
    from .openai_responses import OpenAIProvider

    gateway = Gateway(db)
    gateway.register(OpenAICompatProvider("llama-swap", llama_swap_url, locality="local"))
    gateway.register(AnthropicProvider(lambda: secrets.get(ANTHROPIC_API_KEY)))
    gateway.register(GeminiProvider(lambda: secrets.get(GEMINI_API_KEY)))
    gateway.register(OpenAIProvider(lambda: secrets.get(OPENAI_API_KEY)))
    gateway.register(DeepSeekProvider(lambda: secrets.get(DEEPSEEK_API_KEY)))
    gateway.register(OpenRouterProvider(lambda: secrets.get(OPENROUTER_API_KEY),
                                        lambda: db.acall(lambda conn: chosen_models(conn, "openrouter"))))  # fmt: skip
    gateway.register(ClaudeCodeProvider())
    return gateway
