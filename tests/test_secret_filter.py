"""The secret filter (D10, D27): secret files are recognised, secrets in text are masked, ordinary text is not."""

import pytest

from hub.secret_filter import MASK, is_secret_file, redact


@pytest.mark.parametrize(
    "path",
    [".env", "app/.env.local", "config\\.env.production", "id_ed25519", "certs/server.pem", "keys/deploy.key",
     "credentials.json", "service-account-prod.json", ".git-credentials", "infra/terraform.tfstate", "vault.kdbx",
     # the audit (D46): more stores of logins, keys under other names, the names Windows opens as .env
     ".npmrc", "web/.yarnrc.yml", ".pgpass", "home/.ssh/config", ".ssh/known_hosts", "id_rsa_github",
     "keys/id_ed25519_work", "x/.gnupg/pubring.kbx", ".docker/config.json", "user\\.kube\\config", ".env.", ".env ",
     ".env::$DATA"],
)  # fmt: skip
def test_secret_files(path):
    assert is_secret_file(path)


@pytest.mark.parametrize(
    "path",
    [".env.example", "app/.env.sample", "id_ed25519.pub", "src/env.py", "README.md", "keyboard.py", "token.py",
     ".ssh/id_rsa_work.pub", "config.json", "docker/config.json", "my.kube/config", "app/config"],
)  # fmt: skip
def test_ordinary_files(path):
    assert not is_secret_file(path)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("ANTHROPIC_API_KEY=sk-ant-api03-" + "a" * 40, "ANTHROPIC_API_KEY="),
        ("key = 'sk-proj-" + "B" * 30 + "'", "key = '"),
        ("token: ghp_" + "c" * 36, "token: "),
        ("aws AKIAABCDEFGHIJKLMNOP here", "aws "),
        ("url=postgres://admin:hunter2pass@db:5432/app", "url=postgres://admin:"),
        ('{"client_secret": "s3cr3t-value-123"}', '{"client_secret": "'),
        ("export DB_PASSWORD=correct-horse-battery", "export DB_PASSWORD="),
        ("bot 123456789:AA" + "x" * 33, "bot "),
        (
            "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "Bearer",
        ),
        # the audit (D46): npm, GitLab, Stripe, Tavily, and the Authorization header whatever its token looks like
        ("//registry.npmjs.org/:_authToken npm_" + "D" * 36, "//registry.npmjs.org/:_authToken "),
        ("glpat-" + "e" * 20 + " in the CI", ""),
        ("stripe sk_live_" + "F" * 24, "stripe "),
        ("rk_test_" + "g" * 24, ""),
        ("TAVILY tvly-dev-" + "h" * 24, "TAVILY "),
        ("Authorization: Bearer 9f8e7d6c5b4a3210zz", "Authorization: Bearer "),
        ('curl -H "Authorization: Basic dXNlcjpwYXNzd29yZA==" https://x', 'curl -H "Authorization: Basic '),
    ],
)
def test_secrets_are_masked(text, kept):
    masked, count = redact(text)
    assert count == 1 and MASK in masked and masked.startswith(kept)
    gone = ("sk-ant", "hunter2", "s3cr3t", "horse", "AKIA", "ghp_", "eyJzdWIi", "npm_", "glpat", "_live_", "_test_",
            "tvly", "9f8e7d", "dXNlcj")  # fmt: skip
    assert not any(part in masked for part in gone)


def test_a_private_key_block():
    block = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
    masked, count = redact(f"key:\n{block}\nafter")
    assert masked == f"key:\n{MASK}\nafter" and count == 1


@pytest.mark.parametrize(
    "text",
    [
        "def get_token(self): return self._token",
        "password = os.environ['DB_PASSWORD']",
        "API_KEY=${API_KEY}",
        "secret: true",
        "TOKEN_LIMIT = 4096 tokens per answer",
        # lines of this repository the first version masked: names in code are not secrets
        'token_file = tokens_dir / f"{APP_CLIENT}.token"',
        'ANTHROPIC_API_KEY = "anthropic_api_key"',
        "def create_app(settings: HubSettings, secret_store: SecretStore | None = None) -> FastAPI:",
        "return anthropic.AsyncAnthropic(api_key=api_key, timeout=anthropic.Timeout(600.0))",
        '"cached_input_tokens": cache_read,',
        "see https://example.com/docs for details",
        "the Authorization header carries a Bearer token",
        "Authorization: Bearer token",
        "Привет, мир: обычный текст без секретов",
    ],
)
def test_ordinary_text_is_left_alone(text):
    masked, count = redact(text)
    assert count == 0 and masked == text
