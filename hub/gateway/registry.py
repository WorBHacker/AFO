"""The gateway: holds one adapter per provider, keeps the models table in sync, resolves a model id."""

from __future__ import annotations

import logging

from ..db import Database
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

    def resolve(self, model_id: str) -> tuple[Provider, str]:
        """'llama-swap/coder' -> (provider, 'coder'). The provider part is the name before the first '/'."""
        provider_name, _, model_ref = model_id.partition("/")
        provider = self._providers.get(provider_name)
        if provider is None or not model_ref:
            raise ModelNotFound(f"no provider for model id {model_id!r}")
        return provider, model_ref

    async def sync_catalog(self) -> dict[str, int | str]:
        """Asks every provider for its models and upserts them. One provider failing does not stop the rest."""
        from ..store import upsert_model

        total = 0
        errors: dict[str, str] = {}
        for name, provider in self._providers.items():
            try:
                models = await provider.list_models()
            except ProviderError as exc:
                errors[name] = exc.detail
                log.warning("catalog sync: %s failed: %s", name, exc.detail)
                continue
            if models:
                await self._db.acall(lambda conn, m=models: [upsert_model(conn, model) for model in m])
                total += len(models)
        result: dict[str, int | str] = {"synced": total, "providers": len(self._providers)}
        if errors:
            result["errors"] = "; ".join(f"{k}: {v}" for k, v in errors.items())
        return result


def build_default_gateway(db: Database, llama_swap_url: str) -> Gateway:
    """The gateway as stage 1 configures it: the local llama-swap provider. Cloud providers join later."""
    from .openai_compat import OpenAICompatProvider

    gateway = Gateway(db)
    gateway.register(OpenAICompatProvider("llama-swap", llama_swap_url, locality="local"))
    return gateway
