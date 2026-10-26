"""The model gateway (D5-D7): one catalog of local and cloud models, one adapter per provider."""

from .base import (
    Completion,
    GatewayError,
    Model,
    ModelNotFound,
    ProviderError,
    StreamEvent,
    blocks_to_prompt_text,
    reasoning_block,
    text_block,
)
from .registry import Gateway

__all__ = [
    "Completion",
    "Gateway",
    "GatewayError",
    "Model",
    "ModelNotFound",
    "ProviderError",
    "StreamEvent",
    "blocks_to_prompt_text",
    "reasoning_block",
    "text_block",
]
