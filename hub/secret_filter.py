"""Secrets stay out of what models see (D10, D27): secret files are not read for them at all, and secrets inside
any other text - files, search results, terminal output, process command lines - are masked.

Two layers, because each misses what the other catches:
- by file: `.env` (but not `.env.example`), private keys, credential stores; their content never reaches a model;
- by content: private key blocks, well-known token formats (Anthropic, OpenAI, GitHub, AWS, Google, Slack,
  Telegram, JWT), passwords in connection strings, and `NAME=value` where the name says it is a secret. The name
  stays, so the model still knows the setting exists; the value becomes [REDACTED].

The window shows a person their own files as they are; this filter is for the model-facing paths (context, tools).
"""

from __future__ import annotations

import fnmatch
import re

MASK = "[REDACTED]"

SECRET_FILES = (
    ".env", ".env.*", "*.env", ".envrc", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "*.ppk", "credentials", "credentials.json", "credentials.yml",
    "credentials.yaml", "secrets.json", "secrets.yml", "secrets.yaml", "secret.json", "token.json", ".netrc",
    "_netrc", ".pypirc", ".git-credentials", "service-account*.json", "*.tfstate", "*.tfstate.backup",
)  # fmt: skip
NOT_SECRET = (".env.example", ".env.sample", ".env.template", ".env.dist", "*.pub")

_TOKENS = [
    r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z]+ )*PRIVATE KEY-----",
    r"\bsk-ant-[A-Za-z0-9_\-]{20,}",
    r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bgithub_pat_[A-Za-z0-9_]{40,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\bAIza[0-9A-Za-z_\-]{35}",
    r"\bxox[abposr]-[A-Za-z0-9\-]{10,}",
    r"\b\d{8,10}:AA[A-Za-z0-9_\-]{33}\b",  # a Telegram bot token
    r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",  # a JWT
    r"\bhf_[A-Za-z0-9]{30,}",
]
TOKEN = re.compile("|".join(f"(?:{t})" for t in _TOKENS))
# NAME=value, NAME: value, "name": "value" where the name says it is a secret
ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<name>["']?[A-Za-z0-9_.\-]*(?:secret|token|passw(?:or)?d|passwd|pwd|api[_\-]?key|apikey|private[_\-]?key|
        access[_\-]?key|auth[_\-]?key|client[_\-]?secret|credentials?)[A-Za-z0-9_.\-]*["']?)
    (?P<sep>\s*(?:=|:)\s*)
    (?P<quote>["']?)(?P<value>[^\s"'#,;}{()\[\]<>`]{6,})(?P=quote)
    """
)
# scheme://user:password@host
URL_PASSWORD = re.compile(r"(?P<head>\b[a-z][a-z0-9+.\-]*://[^:/\s@]+:)(?P<value>[^@\s/]{3,})(?P<tail>@)", re.I)
CODE_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
WORD = re.compile(r"[A-Za-z_]+")
NOT_A_VALUE = {"true", "false", "null", "none", "required", "optional", "string", "example", "changeme", "xxxxxx"}


def is_secret_file(path: str) -> bool:
    """Whether a file (a path inside a project, any separator) is one whose content a model never gets."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if any(fnmatch.fnmatch(name, pattern) for pattern in NOT_SECRET):
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_FILES)


def redact(text: str) -> tuple[str, int]:
    """The text with its secrets masked, and how many were masked."""
    count = 0

    def token(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return MASK

    def assignment(match: re.Match[str]) -> str:
        nonlocal count
        value = match.group("value")
        if (
            value.lower() in NOT_A_VALUE
            or value == MASK
            or value.startswith(("${", "$(", "%", "<"))
            or any(c in value for c in "([")  # code: a call or an index, e.g. os.environ['DB_PASSWORD']
            or CODE_NAME.fullmatch(value)  # code: self._token, settings.api_key
            or WORD.fullmatch(value)  # a name in code (api_key=api_key, secret: SecretStore): secrets have digits
        ):
            return match.group(0)  # a placeholder or a reference to somewhere else, not a secret
        count += 1
        quote = match.group("quote")
        return f"{match.group('name')}{match.group('sep')}{quote}{MASK}{quote}"

    def url(match: re.Match[str]) -> str:
        nonlocal count
        if WORD.fullmatch(match.group("value")):
            return match.group(0)  # user:password@ - a placeholder, not a password
        count += 1
        return f"{match.group('head')}{MASK}{match.group('tail')}"

    text = TOKEN.sub(token, text)
    text = URL_PASSWORD.sub(url, text)
    text = ASSIGNMENT.sub(assignment, text)
    return text, count


def clean(text: str) -> str:
    return redact(text)[0]
