"""App settings, stored as JSON in %APPDATA%\\LocalChat\\settings.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LocalChat"
SETTINGS_FILE = APP_DIR / "settings.json"
CHATS_DIR = APP_DIR / "chats"


@dataclass
class Settings:
    base_url: str = "http://127.0.0.1:8080"
    llama_swap_exe: str = "D:/AI model/llama-swap/llama-swap.exe"
    llama_swap_config: str = "D:/AI model/llama-swap/config.yaml"
    autostart_llama_swap: bool = True
    system_prompt: str = ""
    reasoning_effort: str = "medium"
    last_model: str = "flash-next"
    window_geometry: str = ""

    @classmethod
    def load(cls) -> Settings:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
