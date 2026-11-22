"""The engines a model runs on under llama-swap (stage 45): one entry asks each of them the same questions - whose
command a model's block is, what of the model the window changes, whether AFO measures it, whether its file's header
is read (the memory estimate, the drafts made for it, the chat template's depths). llama.cpp is the first; Strata is
known by its command and takes only its ttl until 45.1в.

The engine's own work stays where it is: llama.cpp's flags and their names in `llama_config.FLAGS`, llama-bench in
`hub/tuning.py`, the header in `hub/gguf.py`. The name `hub/engines.py` is taken by llama.cpp's download."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

LLAMA_CPP = "llama.cpp"
SD = "stable-diffusion.cpp"  # a picture model's sd-server (D55, 29.3): the visual module's settings, not the window's
STRATA = "strata"
OTHER = "other"

TTL = "ttl"  # llama-swap's own: every engine's model has it
# what the window changes of an llama.cpp model whose command is on lines of its own (llama_config.check_changes)
LLAMA_SETTINGS = ("context", "gpu_layers", "flash_attn", "cache_type", "parallel",
                  "batch", "ubatch", "threads", "n_cpu_moe",  # llama_config.COUNTS
                  "cpu_moe", "moe_cache_mib",  # llama_config.MOE without n_cpu_moe, which is a count
                  "spec_type", "draft_model", "draft_gpu_layers", "draft_max", "draft_cache_type")  # fmt: skip


@dataclass(frozen=True)
class Runtime:
    name: str  # as model_view's "engine" says it
    program: str = ""  # a word of the command has it: the engine's program (llama-server.exe, a quoted path to it)
    word: str = ""  # a whole word of the command: the engine's subcommand (`strata serve --profile …`)
    marked: str = ""  # or a line of the block says `engine: <it>` (an sd model whose program is a macro)
    flags: bool = False  # its launch settings are its command's flags: the window changes them (llama_config.edit)
    settings: tuple[str, ...] = ()  # what the window changes beside the ttl, when it changes the flags
    measured: bool = False  # tuning measures it: llama-bench, the experts on the processor, guessing ahead
    header: bool = False  # its model file's GGUF header is read: the memory estimate, the chat template's depths
    drafts: bool = False  # draft models are fetched for it, guessing ahead (42.2)

    def runs(self, tokens: list[str], block: list[str]) -> bool:
        """Whether a model's command (its words, the macros put in) and block are this engine's."""
        return bool(
            (self.program and any(self.program in t for t in tokens))
            or (self.word and self.word in tokens)
            or (self.marked and any(re.match(rf"^\s+engine:\s*{re.escape(self.marked)}\s*$", line) for line in block))
        )


LLAMA = Runtime(LLAMA_CPP, program="llama-server", flags=True, settings=LLAMA_SETTINGS, measured=True, header=True,
                drafts=True)  # fmt: skip
PICTURES = Runtime(SD, program="sd-server", marked=SD)
STRATA_RUNTIME = Runtime(STRATA, word="serve")  # its profile is Strata's: the ttl only (45.1в: what it lets change)
RUNTIMES: tuple[Runtime, ...] = (LLAMA, PICTURES, STRATA_RUNTIME)  # asked in this order: the first that runs it
_OTHER = Runtime(OTHER)


def engine_of(tokens: list[str], block: list[str]) -> Runtime:
    """The engine that runs a model's command; one AFO does not know - OTHER, nothing of it changed but the ttl."""
    return next((runtime for runtime in RUNTIMES if runtime.runs(tokens, block)), _OTHER)


def of(view: dict[str, Any]) -> Runtime:
    """The engine of a model as model_view read it."""
    return next((runtime for runtime in RUNTIMES if runtime.name == view.get("engine")), _OTHER)


def settings(view: dict[str, Any]) -> frozenset[str]:
    """What the window changes of a model: the ttl - and its engine's settings when its command is on lines of its
    own (view["editable"]; one line - they are changed in config.yaml itself)."""
    return frozenset((TTL, *(of(view).settings if edits(view) else ())))


def edits(view: dict[str, Any]) -> bool:
    """Whether the window changes the flags of a model's command."""
    return of(view).flags and bool(view.get("editable"))


def measured(view: dict[str, Any]) -> bool:
    """Whether tuning measures a model: its engine is measured and the window changes its command."""
    return of(view).measured and edits(view)


def estimated(view: dict[str, Any]) -> bool:
    """Whether a model's memory is estimated from its file's header: its engine's file, named in the command."""
    return of(view).header and bool(view.get("file"))


def drafted(view: dict[str, Any]) -> bool:
    """Whether draft models are fetched for a model: its engine guesses ahead with one, its file named."""
    return of(view).drafts and bool(view.get("file"))


def templated(view: dict[str, Any]) -> bool:
    """Whether a model's chat template is read (the depths of thinking it takes): from its command's flags or file."""
    return of(view).flags and of(view).header
