"""The model checks its own page («Стабилизация», portion 5; the user's complaint 7 of 2026-11-27): page_check opens
it in the real Edge without a window at two or three widths and says in words what is wrong - the console's errors,
the CSS the browser dropped, blocks past the edge or of zero size, pictures not shown; a model that sees pictures
gets the page's picture too. Nothing is fetched from the internet."""

import base64
import re

import pytest
from PySide6.QtGui import QImage

from hub import chat as chat_module
from hub import pagecheck, snapshot
from hub.gateway.base import Model
from tests.test_autonomous import Scripted, use
from tests.test_changes import finished
from tests.test_snapshot import pages  # noqa: F401 - the fixture: a server of this computer
from tests.test_tools import hub  # noqa: F401 - the hub fixture

needs_edge = pytest.mark.skipif(snapshot.edge() is None, reason="Microsoft Edge is not on this computer")

STYLE = """/* a comment; with { braces } */
.card { colr: red; display: flexbox; color: var(--ink) !important; --ink: #123; -moz-appearance: none }
.a:hovr { color: blue }
.wide { width: 600px; height: 20px; background: url("data:image/svg+xml;utf8,<svg></svg>") }
.z { height: 0; overflow: hidden }
@media (max-width: 500px) { .b { gap: 2px; colour: x } }
@font-face { font-family: X; src: url(x.woff2); font-display: swap }
@keyframes spin { from { transform: rotate(0deg) } to { transfrom: rotate(1turn) } }
.n { color: red; &:hover { color: bleu } }
@tailwind base;
"""
PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="style.css"><link rel="stylesheet" href="https://fonts.example.com/inter.css">
</head><body style="margin: 0">
<div class="card">карточка</div><div class="wide">широкий</div><div class="z">не видно</div>
<p style="widht: 3px">абзац</p><img src="missing.png" alt="">
<script src="app.js"></script></body></html>"""
APP = "console.error('bad thing', 42);\ndocument.getElementById('nope').addEventListener('click', () => {});\n"
CLEAN = ("<!doctype html><html><head><meta name='viewport' content='width=device-width'><style>body { margin: 0; "
         "font: 16px sans-serif } main { max-width: 900px; margin: auto; padding: 8px }</style></head><body><main>"
         "<h1>Готово</h1><p>Всё на месте.</p></main></body></html>")  # fmt: skip


def site(root):
    (root / "site").mkdir(parents=True, exist_ok=True)
    phone = CLEAN.replace("<meta name='viewport' content='width=device-width'>", "")
    files = {"index.html": PAGE, "style.css": STYLE, "app.js": APP, "clean.html": CLEAN, "phone.html": phone}
    for name, text in files.items():
        (root / "site" / name).write_bytes(text.encode())  # LF, as written by a model
    return root / "site"


def test_the_css_a_page_asks_for_is_read_piece_by_piece():
    """The pieces the browser is asked about, with their lines: the selectors of style rules (not a nested rule's,
    not a keyframe's), the declarations (not a custom property, not another browser's own, `!important` aside; a
    `;` inside url(...) or a string is not an end), at-rules no browser holds and what stands outside any rule; the
    descriptors of @font-face are not properties."""
    items = pagecheck.css_items(STYLE, "style.css")
    seen = [(i.where, i.kind, i.text) for i in items]
    assert ("style.css:2", "rule", ".card") in seen and ("style.css:3", "rule", ".a:hovr") in seen
    decls = {i.text: i for i in items if i.kind == "decl"}
    assert decls["colr: red"].rule == ".card" and decls["colr: red"].where == "style.css:2"
    assert decls["color: var(--ink) !important"].value == "var(--ink)"
    assert not any(t.startswith(("--ink", "-moz-")) for t in decls)
    assert decls['background: url("data:image/svg+xml;utf8,<svg></svg>")'].prop == "background"
    assert decls["colour: x"].where == "style.css:6" and decls["colour: x"].rule == ".b"
    assert "font-family: X" not in decls and "src: url(x.woff2)" not in decls  # descriptors
    assert decls["transfrom: rotate(1turn)"].rule == "to" and ("style.css:8", "rule", "from") not in seen
    assert decls["color: bleu"].rule == ".n" and not any(t == "&:hover" for _w, _k, t in seen)  # nested: relative
    assert ("style.css:10", "at", "@tailwind") in seen
    inline = pagecheck.css_items("widht: 3px; color: red", "the style attribute of `p`:", inside="")
    attribute = "the style attribute of `p`:"
    assert [(i.where, i.kind, i.prop, i.rule) for i in inline] == [(attribute, "decl", "widht", ""),
                                                                   (attribute, "decl", "color", "")]  # fmt: skip
    assert next(i.kind for i in pagecheck.css_items("color: red; .x { a: b }", "s.css")) == "stray"


@needs_edge
def test_a_page_with_mistakes_is_told_back_at_each_width(tmp_path):
    """The real Edge: a script's error and console.error with their places, a file not there, a sheet of the internet
    (not fetched - said apart: not the page's own error), every dropped piece of CSS and nothing of what the browser
    takes; at 375 px the block of 600 px goes past the right edge and the page is wider than the phone, not at 1280;
    a box of zero height that holds text, a picture that did not load (once, not as of zero size too). The picture
    of the first width when asked, and Edge is gone after the check."""
    page = snapshot.page_file(site(tmp_path), "index.html")
    before = {p.pid for p in snapshot._left("afo-check-")}
    found, shot = pagecheck.check(page, [1280, 375], picture=True)
    text = pagecheck.report(found, "site/index.html", picture=True)
    assert text.startswith("Page check of site/index.html at 1280, 375 px wide"), text
    assert "console.error: bad thing 42 - app.js:1" in text
    assert ("script error: Uncaught TypeError: Cannot read properties of null (reading 'addEventListener') - "
            "app.js:2") in text  # fmt: skip
    assert "not loaded: missing.png (no such file)" in text
    assert "Not loaded from the internet" in text and "https://fonts.example.com/inter.css" in text
    for said in ("style.css:2 `colr: red` in `.card` - there is no such property",
                 "style.css:2 `display: flexbox` in `.card` - the browser does not take this value",
                 "style.css:3 `.a:hovr` - the browser cannot read this selector",
                 "style.css:6 `colour: x` in `.b` - there is no such property",
                 "style.css:8 `transfrom: rotate(1turn)` in `to` - there is no such property",
                 "style.css:9 `color: bleu` in `.n` - the browser does not take this value",
                 "style.css:10 `@tailwind` - the browser does not know this at-rule",
                 "the style attribute of `p`: `widht: 3px` - there is no such property"):  # fmt: skip
        assert said in text, (said, text)
    for fine in ("var(--ink)", "gap: 2px", "font-display", 'url("data', "--ink", "-moz-"):
        assert fine not in text.split("CSS the browser dropped")[1].split("At ")[0], (fine, text)
    narrow = text.split("At 375 px:")[1]
    assert "the page is 600 px wide, wider than the phone's screen" in narrow
    assert "`div.wide` goes past the right edge: from 0 to 600 px" in narrow
    assert "`div.z` is 375x0 px - zero size" in narrow and '`img[src="missing.png"]` - the picture is not shown' in text
    wide = text.split("At 1280 px:")[1].split("At 375 px:")[0]
    assert "goes past" not in wide and wide.count("missing.png") == 1  # not shown - not «zero size» as well
    assert text.endswith("The page's picture at 1280x800 comes to you after this result.")
    image = QImage.fromData(shot, "PNG")
    assert (image.width(), image.height()) == (1280, 800)
    assert {p.pid for p in snapshot._left("afo-check-")} <= before


@needs_edge
def test_a_clean_page_is_told_clean_and_a_phone_hears_of_the_viewport(tmp_path, pages):  # noqa: F811
    """Nothing wrong - said so; a page of this computer's server is checked as well; a page without <meta
    name="viewport"> is laid out 980 px wide on a phone - said at the narrow width. No picture unless asked."""
    root = site(tmp_path)
    found, shot = pagecheck.check(snapshot.page_file(root, "clean.html"), [1280, 768, 375], picture=False)
    text = pagecheck.report(found, "site/clean.html", picture=False)
    assert shot is None and text.endswith("Nothing found: no console errors, no CSS dropped, nothing past the edge or "
                                          "of zero size, every picture shown."), text  # fmt: skip
    found, _ = pagecheck.check(snapshot.page_file(root, "phone.html"), [1280, 375], picture=False)
    text = pagecheck.report(found, "site/phone.html", picture=False)
    assert re.search(r'At 375 px:\n- there is no <meta name="viewport">: a phone lays the page out 98[01] px wide',
                     text), text  # fmt: skip
    served = f"http://127.0.0.1:{pages.port}/"
    found, _ = pagecheck.check(served, [800], picture=False)
    assert found["widths"][0].loaded and pages.asked
    assert pagecheck.widths_of({}) == [1280, 768, 375] and pagecheck.widths_of({"widths": [640, 640]}) == [640]
    for wrong in ([100], [1, 2, 3, 4], "1280", [True]):
        with pytest.raises(snapshot.SnapshotError):
            pagecheck.widths_of({"widths": wrong})


TOOLS_OF = "SELECT tools FROM conversations WHERE id = ?"


class Eyes(Scripted):
    """Scripted, its model seeing pictures or not."""

    def __init__(self, name, script, vision):
        super().__init__(name, script)
        self.models = [Model(f"{name}/m", name, "m", name.title(), "local", {"vision": vision, "context": 65536}, {})]


@needs_edge
def test_a_chat_model_checks_its_page_and_one_that_sees_gets_its_picture(hub, repo):  # noqa: F811
    """In a chat with a project (and so in a goal, whose turns are the chat's): page_check is among the tools; the
    page the model wrote is checked, the words come as the call's result, the picture is kept with the chat (shown
    under the call) and comes to a model that sees pictures with its next request - a model that does not see them
    gets the words alone. AFO's own pages are not opened."""
    own = hub.app.state.settings.port
    write = ("write_file", {"project": "proj", "path": "site/index.html", "content": PAGE})
    check = ("page_check", {"project": "proj", "path": "site/index.html", "widths": [1024, 375]})
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    for vision in (True, False):
        name = "eyes" if vision else "blind"
        model = use(hub, Eyes(name, [[write], [check, ("page_check", {"url": f"http://127.0.0.1:{own}/v1/runs"})],
                                     "Вижу."], vision))  # fmt: skip
        conv = hub.post("/v1/conversations", json={}).json()["id"]
        assert hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [pid]}).status_code == 200
        assert hub.put(f"/v1/conversations/{conv}/context", json={"mode": "trusted"}).status_code == 200
        sent = hub.post(f"/v1/conversations/{conv}/messages", json={"content": "Сделай страницу и проверь её",
                                                                    "model_id": f"{name}/m"})  # fmt: skip
        assert sent.status_code == 202, sent.text
        messages = finished(hub, conv)
        results = [b for m in messages for b in m["content"] if b.get("type") == "tool_result"]
        checked, refused = [b for b in results if b["name"] == "page_check"]
        assert checked["error"] is False and checked["output"].startswith("Page check of site/index.html of «proj»")
        assert "At 375 px:" in checked["output"] and "not loaded: style.css (no such file)" in checked["output"]
        assert refused["error"] is True and "AFO's own" in refused["output"]
        tools = hub.app.state.db.call(lambda c, conv=conv: c.execute(TOOLS_OF, (conv,)).fetchone()["tools"])
        assert '"name": "page_check"' in tools
        after = [m for m in model.prompts[-1] if m.get("images")]
        if vision:
            assert checked["blob"] and checked["media_type"] == "image/png"  # kept: the window shows it under the call
            assert checked["output"].endswith("The page's picture at 1024x800 comes to you after this result.")
            (shown,) = after
            assert shown["content"] == chat_module.SNAPSHOTS_SHOWN
            image = QImage.fromData(base64.b64decode(shown["images"][0]["data"]), "PNG")
            assert (image.width(), image.height()) == (1024, 800)
        else:
            assert not checked.get("blob") and not after and "picture at" not in checked["output"]
