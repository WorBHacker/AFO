"""The name AFO (step 10.4b, Q18): people see only AFO, never spelled out; keys kept under the old name (LocalChat)
move to AFO's. Machine names - headers, file kinds, the MCP name inside Claude Code, branches, settings keys - stay as
they were, so nothing already set up breaks."""

import ast
import json
import re
from pathlib import Path

from hub import secrets

ROOT = Path(__file__).resolve().parent.parent
KEPT = ("X-LocalChat-", "LocalChat.Scripts", "(?:AFO|LocalChat)")  # machine names, and markers that find the old ones
OLD = {"LocalChat", "LocalChat hub"}  # the old names themselves: what is under them moves to AFO's
SPELLED = re.compile(r"all\s+for\s+one|allforone", re.IGNORECASE)


def strings(path: Path) -> list[str]:
    """The string literals of a module, docstrings left out."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body
            and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}  # fmt: skip
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]  # fmt: skip


def shown(text: str) -> bool:
    """The old name in a text: not a machine name, not the old name kept to move what is under it."""
    if text in OLD:
        return False
    for kept in KEPT:
        text = text.replace(kept, "")
    return "LocalChat" in text


def test_people_see_only_afo():
    sources = [p for folder in ("hub", "app", "common") for p in (ROOT / folder).rglob("*.py")]
    left = [(p.relative_to(ROOT).as_posix(), s) for p in sources for s in strings(p) if shown(s)]
    assert left == []
    for qml in (ROOT / "app" / "ui" / "qml").glob("*.qml"):
        code = re.sub(r"(^|\s)//.*$", "", qml.read_text(encoding="utf-8"), flags=re.MULTILINE)
        assert "LocalChat" not in code, qml.name
    extension = json.loads((ROOT / "vscode" / "package.json").read_text(encoding="utf-8"))
    assert extension["displayName"] == "AFO" and "LocalChat" not in json.dumps(extension, ensure_ascii=False)
    script = re.sub(r"(^|\s)//.*$", "", (ROOT / "vscode" / "extension.js").read_text(encoding="utf-8"), flags=re.M)
    assert "LocalChat" not in script
    assert (ROOT / "AFO.cmd").is_file() and not (ROOT / "LocalChat.cmd").exists()
    assert (ROOT / "README.md").read_text(encoding="utf-8").startswith("# AFO\n")
    everything = [*sources, *(ROOT / "app" / "ui" / "qml").glob("*.qml"), *(ROOT / "vscode").glob("*.js*"),
                  ROOT / "README.md", ROOT / "README.en.md", ROOT / "TechDoc.md",
                  ROOT / "AFO.cmd"]  # fmt: skip
    assert [p.name for p in everything if SPELLED.search(p.read_text(encoding="utf-8"))] == []  # AFO, not the words


def test_keys_under_the_old_name_move_to_afo(monkeypatch):
    """The OS store is a dict here: the Credential Manager is never touched."""
    import keyring
    from keyring.errors import PasswordDeleteError

    vault = {("LocalChat", "telegram_token"): "123:abc", ("LocalChat", "gone"): "x"}

    def delete(service, name):
        if vault.pop((service, name), None) is None:
            raise PasswordDeleteError(name)

    monkeypatch.setattr(keyring, "get_password", lambda service, name: vault.get((service, name)))
    monkeypatch.setattr(keyring, "set_password", lambda service, name, value: vault.__setitem__((service, name), value))
    monkeypatch.setattr(keyring, "delete_password", delete)
    store = secrets.KeyringSecretStore()
    assert (store.service, secrets.OLD_SERVICE) == ("AFO", "LocalChat")
    assert store.get("telegram_token") == "123:abc"
    assert vault == {("AFO", "telegram_token"): "123:abc", ("LocalChat", "gone"): "x"}
    assert store.get("telegram_token") == "123:abc" and store.get("nothing") is None
    assert store.delete("gone") and vault == {("AFO", "telegram_token"): "123:abc"}  # not back from the old name later
    assert not store.delete("gone")
    store.set("tavily_api_key", "t")
    assert vault[("AFO", "tavily_api_key")] == "t"
