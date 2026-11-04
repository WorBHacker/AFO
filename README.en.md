# AFO

English · [Русский](README.md)

A local AI ecosystem for Windows: a **hub** that knows your projects, processes, terminals and models, and a **light
window** on Qt Quick (QML, drawn by the CPU — the graphics card stays with the models). VS Code, Claude Code and agents
connect to the hub. The window speaks English and Russian (Settings → Язык · Language).

Until 2026-11-04 the program was called LocalChat (its working name); the project folder and the internal names keep
it: the packages (`python -m hub`), the `LOCALCHAT_*` variables, headers and file formats. Every decision and stage is
in [ARCHITECTURE.md](ARCHITECTURE.md) (in Russian).

## Install

Download `AFO-Setup-<version>.exe` from [Releases](https://github.com/WorBHacker/AFO/releases) and run it. The usual
Windows wizard (Russian or English) installs AFO for the current user in `%LOCALAPPDATA%\Programs\AFO`, no
administrator rights needed. The installer is not signed yet, so Windows shows “Windows protected your PC”: click
“More info” → “Run anyway”. Windows 10 or 11, 64-bit. Uninstall in Settings → Apps → Installed apps; your data and
downloaded models stay unless you ask to delete them.

On a computer with no local models yet, the window opens the setup wizard by itself: it shows the graphics card and
memory, downloads a suitable build of llama.cpp and llama-swap from GitHub (checking their sha256), starts them and
offers a first model for chat, code and images. Open it again with Settings → Setup wizard….

## What it does

- **Local models**: Settings → Install a local model… (or the last item of the model picker). In “Picks” each model
  already has the variant that runs best on this computer, with whether it fits in VRAM; “Search Hugging Face” finds
  any GGUF model. The hub downloads it to the fastest disk with room (resuming, checking sha256), writes it into
  llama-swap's `config.yaml` (a copy of the old one next to it) and tries to start it.
- **Cloud models and keys**: API keys (Anthropic, OpenAI, Google Gemini, DeepSeek, OpenRouter, Tavily, Brave Search),
  your own SearXNG and a Telegram bot's token go in Connections (Ctrl+Shift+K), behind a connections password; keys
  live in the Windows Credential Manager and never leave the hub.
- **MCP servers**: outside MCP servers connect by command (`npx -y @modelcontextprotocol/server-…`, `uvx …`) or
  address; what only reads runs without a question, everything else needs your approval.
- **The autonomous agent**: works toward a goal on its own; can control the windows of programs you allow
  (Settings → Programs for the agent…) through UI Automation, after your approval, never touching password fields or
  payments. A frame and a “Stop” bar surround the window while it acts; Ctrl+Alt+End stops it from anywhere. Tasks can
  run on a timer or on an event (a new file in a folder, a message to your Telegram bot); nothing starts in the quiet
  hours.
- **Mail for the agent** (any IMAP/SMTP mailbox; it sends only after your “yes”) and **API keys for the agent** (a key
  goes only to its own site; the model never sees it).
- **Transfer settings**: all settings in one file to load on another computer — no keys, passwords or chats in it.
- **Check the system…** (Settings → Hub): what is fine, what is not and what to do; “Collect logs for support” (no keys,
  no chats).

## Structure

| Folder | What it is |
|---|---|
| `hub/` | the hub — a background process on `127.0.0.1:8765`: API and WebSocket, database, model gateway (llama-swap, Claude, Claude Code, OpenAI, Gemini, DeepSeek, OpenRouter), projects, terminals, approvals, agents, MCP server, OpenAI-compatible server |
| `app/` | the PySide6 window: `app/ui/` — Qt Quick (QML), drawn by the CPU; the hub's client, starts the hub when it is not running |
| `common/` | shared code: data paths, logging, translation (`common/i18n.py`, `common/locale/`) |
| `vscode/` | the VS Code extension: the open file and the terminals' commands — to the hub |
| `installer/` | AFO's installer: the program's folder and the Inno Setup wizard (`afo.iss`) |
| `tests/` | tests (pytest) |
| `data/` | the database, its copies, logs, snapshots — made by itself, not in git |

## Development

Python 3.14:

```bash
py -3.14 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[hub,app,dev]"
.venv/Scripts/python.exe -m pip install -r requirements.lock -e . --no-deps   # exactly the tested versions
```

Checks:

```bash
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m pytest -m stress        # the stress bench (~3 min)
.venv/Scripts/python.exe -m common.i18n_catalog     # the English catalog: nothing missing, nothing extra
.venv/Scripts/pip-audit.exe -r requirements.lock    # known vulnerabilities of the dependencies
```

Run the window: double-click `AFO.cmd` (no console) or `.venv/Scripts/python.exe -m app`. The window starts the hub
when it is not running; the hub keeps running after the window closes. The hub by hand:
`.venv/Scripts/python.exe -m hub` and `.venv/Scripts/python.exe -m hub stop`.

Build the installer (Inno Setup 6, once):

```bash
winget install --id JRSoftware.InnoSetup -e --scope user
.venv/Scripts/python.exe -m installer.build             # -> build/dist/AFO-Setup-<version>.exe
```

Inside: the embeddable Python from python.org (its sha256 checked against the published one), the libraries exactly
as in `requirements.lock`, and the code of `hub`, `app`, `common`; the built folder is checked
(`python -m app --smoke`) before it is packed.

**Translation.** Russian is the source: the text in the code is the key of the English catalog
`common/locale/en.json`. Python marks texts with `tr()`, `plural()` and `N_()`; QML with `qsTr()`. A new or changed
text needs its English in the catalog — `python -m common.i18n_catalog --sync` adds the missing keys, and the tests
fail until each has a translation.

## License

AFO is free software: you can use, study, change and share it under the GNU General Public License, version 3 or (at
your option) any later version — see [LICENSE](LICENSE). Whoever shares AFO or a changed AFO shares its source code
under the same license.

The libraries the installer brings along keep their own licenses, all compatible with GPL 3: Qt (PySide6) and pyte —
LGPL 3; the rest — MIT, BSD, Apache 2.0, MPL 2.0 and PSF; their texts lie next to them in the program's folder. The
window's fonts, Inter and JetBrains Mono (SIL OFL 1.1), are in `app/ui/fonts`, the Lucide icons (ISC) in
`app/ui/icons`. The engines the setup wizard downloads separately (llama.cpp, llama-swap, ripgrep) and the models
are separate programs and data with their own licenses.
