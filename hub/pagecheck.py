"""A check of a page the model made, as a browser shows it («Стабилизация», portion 5; the user's complaint 7 of
2026-11-27: the model does not see what is wrong with its own page).

Microsoft Edge without a window, as for page_snapshot (hub/snapshot.py: its own empty profile in a temporary folder,
the internet cut off, only this computer's pages and a project's or a task's HTML files), but driven through its
DevTools (`--remote-debugging-port=0`: a port of this computer only, for the few seconds of the check, chosen by Edge
and written in the profile's folder). The page is opened at two or three window widths and told back in words: the
errors of its console (scripts, files not loaded), the CSS the browser dropped (an unknown property, a value it does
not take, a selector it cannot read, an at-rule it does not know), blocks past the window's edge or of zero size,
pictures not shown. A model that sees pictures gets the page's picture at the first width as well."""

from __future__ import annotations

import base64
import contextlib
import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from . import snapshot
from .snapshot import SnapshotError

NAME = "page_check"
WIDTHS = (1280, 768, 375)
HEIGHT = 800
MOBILE = 600  # narrower than this: a phone - the page's <meta name="viewport"> counts
TIMEOUT_S = 45.0
OPEN_S = 15.0  # Edge's DevTools open in 0.3 s here (2026-11-28)
LOAD_S = 15.0  # a page that does not end loading in this time is checked as it is
SETTLE_S = 1.0  # after its load: the page's scripts' own time
SHOWN = 12  # of each kind of problem in the answer
CHARS = 6000  # of the whole answer
ITEMS = 4000  # CSS declarations and selectors weighed
MESSAGE = 300  # characters of one console message

PAGE_CHECK: dict[str, Any] = {
    "name": NAME,
    "description": "Check a web page you made the way a browser shows it (Edge without a window): the errors of its "
    "console - scripts, files not loaded; the CSS the browser dropped - an unknown property, a value it does not "
    "take, a selector it cannot read; blocks past the window's edge or of zero size; pictures not shown - at two or "
    "three window widths. A page of this computer (`url`: http://localhost:PORT/...) or an HTML file (`path`) of "
    "your project or of a task's copy (`task_id`). If you see pictures, the page's picture at the first width comes "
    "after the result. Use it after you make or change a page. The internet is cut off for it: what a page takes "
    "from the internet is not loaded.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "A page of this computer: http://localhost:PORT/..."},
            "path": {"type": "string", "description": "An HTML file, as the project's or the task's files name it."},
            "task_id": {"type": "string", "description": "The task whose copy holds `path`, as your batch named it."},
            "project": {"type": "string", "description": "Which project, when there are several."},
            "widths": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Up to three window widths in pixels, 320 to 1920; 1280, 768 and 375 if not given. "
                "The picture is of the first.",
            },
        },
    },
}

# at-rules: those that hold rules, those that hold descriptors (not properties), the statements; any other is dropped
GROUPS = frozenset({"media", "supports", "layer", "container", "scope", "starting-style", "document"})
DESCRIPTORS = frozenset({"font-face", "page", "property", "counter-style", "font-palette-values",
                         "font-feature-values", "view-transition", "color-profile", "position-try"})  # fmt: skip
FRAMES = frozenset({"keyframes", "-webkit-keyframes"})
STATEMENTS = frozenset({"import", "charset", "namespace", "layer"})
OTHERS = ("-moz-", "-ms-", "-o-")  # other browsers' own properties and selectors: there for them, not by mistake
AT_NAME = re.compile(r"@([-\w]+)")
IMPORTANT = re.compile(r"!\s*important\s*$", re.IGNORECASE)
OTHERS_SELECTOR = re.compile(r":-(?:moz|ms|o)-", re.IGNORECASE)

# weighed in the page: 0 - taken; 1 - a selector it cannot read; 2 - a value it does not take; 3 - no such property
WEIGH_JS = """(items) => items.map(it => {
  if (it.s !== undefined) {
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(it.s + '{}');
      return sheet.cssRules.length === 1 ? 0 : 1;
    } catch (e) { return 1; }
  }
  if (CSS.supports(it.p, it.v)) return 0;
  return CSS.supports(it.p, 'inherit') ? 2 : 3;
})"""

# the page's layout at this width (`asked`): its own width, what sticks out, what is of zero size, the pictures not
# shown. A phone zooms a page out to show all of it: with <meta name="viewport"> the screen's width is the edge;
# without it the page is laid out 980 px wide, and that is its edge
LAYOUT_JS = """((asked) => {
  const viewport = !!document.querySelector('meta[name="viewport"]');
  const W = viewport ? Math.min(innerWidth, asked) : innerWidth, doc = document.documentElement, body = document.body;
  const css = e => getComputedStyle(e);
  const name = e => {
    let s = e.tagName.toLowerCase();
    if (e.id) return s + '#' + e.id;
    const c = [...e.classList].slice(0, 2);
    if (c.length) s += '.' + c.join('.');
    const src = e.getAttribute('src');
    if (src) s += '[src="' + src.slice(0, 80) + '"]';
    return s;
  };
  const clipped = e => {
    for (let p = e.parentElement; p && p !== body && p !== doc; p = p.parentElement) {
      if (css(p).overflowX !== 'visible') return true;
    }
    return false;
  };
  const under = (e, seen) => { for (let p = e.parentElement; p; p = p.parentElement) if (seen.has(p)) return true;
                               return false; };
  const media = /^(img|svg|canvas|video|iframe|object|embed)$/;
  const skip = new RegExp('^(script|style|link|meta|title|head|noscript|template|br|wbr|source|track|option|'
                          + 'datalist|param|area|map)$');
  const clips = [css(doc).overflowX, body ? css(body).overflowX : 'visible'];
  const out = {width: W, laid: innerWidth, scroll: Math.max(doc.scrollWidth, body ? body.scrollWidth : 0),
               cut: clips.some(v => v === 'hidden' || v === 'clip'), viewport: viewport, past: [], zero: [],
               broken: [], styles: []};
  const past = new Set(), zero = new Set();
  for (const e of (body ? [...body.querySelectorAll('*')] : []).slice(0, 4000)) {
    const tag = e.tagName.toLowerCase();
    if (skip.test(tag) || (tag !== 'svg' && e.closest('svg'))) continue;
    if (!e.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
    if (tag === 'img' && e.complete && e.naturalWidth === 0 && e.getAttribute('src')) {
      out.broken.push(name(e));
      continue;
    }
    const r = e.getBoundingClientRect(), s = css(e);
    const off = r.right > W + 1 || (r.left < -1 && (s.position === 'static' || s.position === 'relative'));
    if (r.width > 0 && r.height > 0 && s.position !== 'fixed' && off && !clipped(e) && !under(e, past)) {
      past.add(e);
      out.past.push({what: name(e), left: Math.round(r.left), right: Math.round(r.right)});
    }
    if ((r.width === 0 || r.height === 0) && s.display !== 'contents' && !under(e, zero)) {
      const holds = (e.textContent || '').trim() !== '' || e.querySelector('img,svg,canvas,video,iframe') !== null;
      if (media.test(tag) || (holds && (s.overflowX !== 'visible' || s.overflowY !== 'visible'))) {
        zero.add(e);
        out.zero.push({what: name(e), w: Math.round(r.width), h: Math.round(r.height)});
      }
    }
  }
  out.styles = [...document.querySelectorAll('[style]')].slice(0, 200).map(e => [name(e), e.getAttribute('style')]);
  return out;
})"""


@dataclass
class Item:
    """A piece of CSS the browser is asked about: a style rule's selector, a declaration, an at-rule."""

    where: str  # file:line
    kind: str  # "rule" | "decl" | "at" | "stray"
    text: str
    prop: str = ""
    value: str = ""
    rule: str = ""  # the selector a declaration is in


@dataclass
class Width:
    width: int
    loaded: bool
    layout: dict[str, Any] = field(default_factory=dict)


def widths_of(args: dict[str, Any]) -> list[int]:
    """The window widths the model asked for (1-3, each in snapshot.SIZES), or SnapshotError."""
    given = args.get("widths")
    if given in (None, [], ""):
        return list(WIDTHS)
    low, high = snapshot.SIZES["width"]
    if not isinstance(given, list) or not 1 <= len(given) <= 3 or any(
            isinstance(w, bool) or not isinstance(w, int) or not low <= w <= high for w in given):  # fmt: skip
        raise SnapshotError(f"`widths`: one to three whole numbers from {low} to {high}")
    return list(dict.fromkeys(given))


def css_items(text: str, source: str, first_line: int = 1, inside: str | None = None) -> list[Item]:
    """What a style sheet's text asks the browser for (`source`: its name; `inside`: the selector of declarations
    only - a style attribute): the selectors of its style rules (not of nested ones: theirs are relative), their
    declarations (custom properties, other browsers' own and `!important` aside), at-rules no browser holds. The
    descriptors of @font-face and the like are not properties: not weighed."""
    items: list[Item] = []
    stack: list[tuple[str, str]] = [("decls", inside)] if inside is not None else []
    buf: list[str] = []
    start: int | None = None
    line, i, n = first_line, 0, len(text)
    quote, depth = "", 0

    def where(at: int | None) -> str:
        return source if inside is not None else f"{source}:{at or line}"

    def at_rule(piece: str) -> str:
        found = AT_NAME.match(piece)
        return found[1].lower() if found else ""

    def opened(piece: str, at: int | None) -> tuple[str, str]:
        context, selector = stack[-1] if stack else ("rules", "")
        if context == "skip":
            return "skip", ""
        if piece.startswith("@"):
            name = at_rule(piece)
            if name in FRAMES:
                return "frames", ""
            if name in DESCRIPTORS:
                return "skip", ""
            if name in GROUPS:
                return ("decls", selector) if context == "decls" else ("rules", "")
            items.append(Item(where(at), "at", "@" + name))
            return "skip", ""
        if context in ("frames", "decls"):  # a keyframe, a nested rule: declarations, the selector not weighed
            return "decls", selector if context == "decls" else piece
        if not OTHERS_SELECTOR.search(piece):
            items.append(Item(where(at), "rule", piece))
        return "decls", piece

    def ended(piece: str, at: int | None) -> None:
        context, selector = stack[-1] if stack else ("rules", "")
        if not piece or context in ("skip", "frames"):
            return
        if piece.startswith("@"):
            if at_rule(piece) not in STATEMENTS or context == "decls":
                items.append(Item(where(at), "at", "@" + at_rule(piece)))
            return
        if context == "rules":
            items.append(Item(where(at), "stray", " ".join(piece.split())[:120]))
            return
        prop, colon, value = piece.partition(":")
        prop, value = prop.strip(), IMPORTANT.sub("", value.strip()).strip()
        if prop.startswith("--") or prop.lower().startswith(OTHERS):
            return
        text_ = " ".join(piece.split())[:160]
        if not colon:
            items.append(Item(where(at), "stray", text_, rule=selector or ""))
            return
        items.append(Item(where(at), "decl", text_, prop.lower(), value, selector or ""))

    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < n:
                buf.append(text[i + 1])
                line += text[i + 1] == "\n"
                i += 2
                continue
            if c == quote or c == "\n":
                quote = ""
            line += c == "\n"
            i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            buf.append(" ")
            i = end
            continue
        if depth == 0 and c in "{};":
            piece, at = "".join(buf).strip(), start
            buf, start = [], None
            if c == "{":
                stack.append(opened(piece, at))
            elif c == ";":
                ended(piece, at)
            else:
                ended(piece, at)
                if stack:
                    stack.pop()
            i += 1
            continue
        if c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        if c == "\n":
            line += 1
        elif start is None and not c.isspace():
            start = line
        buf.append(c)
        i += 1
    ended("".join(buf).strip(), start)  # the last declaration of a style attribute needs no `;`
    return items


def check(page: str, widths: list[int], *, picture: bool, program: Path | None = None,
          timeout_s: float = TIMEOUT_S) -> tuple[dict[str, Any], bytes | None]:  # fmt: skip
    """What is wrong with `page` at each width (blocking), and its picture at the first when `picture`: Edge without
    a window and its DevTools, its own empty profile, the internet cut off. SnapshotError when Edge is not there,
    the page does not open or the time is out."""
    program = program or snapshot.edge()
    if program is None:
        raise SnapshotError("Microsoft Edge is not found on this computer: page_check needs it")
    folder = Path(tempfile.mkdtemp(prefix="afo-check-"))
    command = [str(program), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-sync", "--disable-background-networking",
               "--disable-component-update", "--hide-scrollbars", f"--proxy-server={snapshot.NOWHERE}",
               f"--user-data-dir={folder / 'profile'}", "--remote-debugging-port=0", "about:blank"]  # fmt: skip
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    deadline = time.monotonic() + timeout_s
    try:
        edge_run = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, creationflags=flags)  # fmt: skip
    except OSError as exc:
        snapshot._remove(folder)
        raise SnapshotError(f"Edge did not start: {exc}") from None
    closed = False
    try:
        found, shot, closed = _checked(folder / "profile" / "DevToolsActivePort", page, widths, picture, deadline)
    except (OSError, WebSocketException) as exc:
        raise SnapshotError(f"Edge's DevTools: {exc}") from None
    finally:
        snapshot._stop(edge_run, folder.name, drawn=closed)
        snapshot._remove(folder)
    return found, shot


class _DevTools:
    """Edge's DevTools over its socket: a command and its answer; the events that came meanwhile, kept."""

    def __init__(self, socket, deadline: float):
        self.socket, self.deadline, self.seq = socket, deadline, 0
        self.events: list[dict[str, Any]] = []

    def _next(self, wait: float) -> dict[str, Any] | None:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise SnapshotError("the page was not checked in time")
        try:
            return json.loads(self.socket.recv(timeout=min(wait, left)))
        except TimeoutError:
            return None

    def call(self, method: str, params: dict[str, Any] | None = None, session: str | None = None) -> dict[str, Any]:
        self.seq += 1
        sent = {"id": self.seq, "method": method, "params": params or {}} | ({"sessionId": session} if session else {})
        self.socket.send(json.dumps(sent))
        while True:
            got = self._next(LOAD_S)
            if got is None:
                raise SnapshotError(f"Edge did not answer ({method})")
            if got.get("id") == self.seq:
                if "error" in got:
                    raise SnapshotError(f"Edge: {method}: {got['error'].get('message', got['error'])}")
                return got.get("result") or {}
            self.events.append(got)

    def wait(self, seconds: float, until: str = "") -> bool:
        """Events for `seconds`, or until one named `until` came (True)."""
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            got = self._next(min(left, 0.25))
            if got is not None:
                self.events.append(got)
                if until and got.get("method") == until:
                    return True
        return False

    def value(self, expression: str, session: str) -> Any:
        found = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session)
        if found.get("exceptionDetails"):
            raise SnapshotError(f"the check's script failed: {found['exceptionDetails'].get('text', '')}")
        return (found.get("result") or {}).get("value")


def _checked(port_file: Path, page: str, widths: list[int], picture: bool, deadline: float
             ) -> tuple[dict[str, Any], bytes | None, bool]:  # fmt: skip
    opened = min(deadline, time.monotonic() + OPEN_S)
    while True:
        with contextlib.suppress(OSError):
            lines = port_file.read_text(encoding="utf-8").split()
            if len(lines) >= 2:
                break
        if time.monotonic() > opened:
            raise SnapshotError("Edge did not open its DevTools in time")
        time.sleep(0.05)
    address = f"ws://127.0.0.1:{lines[0]}{lines[1]}"
    with connect(address, max_size=64 * 2**20, proxy=None, open_timeout=10, ping_interval=None) as socket:
        tools = _DevTools(socket, deadline)
        target = next((t for t in tools.call("Target.getTargets")["targetInfos"] if t.get("type") == "page"), None)
        if target is None:
            raise SnapshotError("Edge opened no page")
        session = tools.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})["sessionId"]
        for domain in ("Page", "Runtime", "Log", "DOM", "CSS"):
            tools.call(f"{domain}.enable", session=session)
        found: dict[str, Any] = {"widths": [], "console": {}, "css": []}
        shot = None
        for k, width in enumerate(widths):
            tools.events.clear()
            tools.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": HEIGHT, "deviceScaleFactor": 1,
                                                              "mobile": width < MOBILE}, session)  # fmt: skip
            went = tools.call("Page.navigate", {"url": page}, session)
            if went.get("errorText"):
                raise SnapshotError(f"the page did not open: {went['errorText']}")
            loaded = tools.wait(LOAD_S, until="Page.loadEventFired")
            tools.wait(SETTLE_S)
            layout = tools.value(f"({LAYOUT_JS})({width})", session) or {}
            found["widths"].append(Width(width, loaded, layout))
            for said in _console(tools.events):
                seen = found["console"].setdefault(said, [])
                if width not in seen:
                    seen.append(width)
            if k == 0:
                found["css"] = _dropped(tools, session, layout.get("styles") or [])
                if picture:
                    shot = base64.b64decode(tools.call("Page.captureScreenshot", {"format": "png"}, session)["data"])
        closed = False  # Edge ends by itself, then its profile goes; its answer may not come before the socket closes
        with contextlib.suppress(WebSocketException, OSError):
            socket.send(json.dumps({"id": tools.seq + 1, "method": "Browser.close"}))
            closed = True
    return found, shot, closed


def _file(url: str) -> str:
    """A file's or page's name as the answer says it: the last part of its address."""
    parts = urlsplit(url or "")
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) if parts.path else ""
    return name or parts.netloc or url or "the page"


def _local(url: str) -> bool:
    parts = urlsplit(url or "")
    return parts.scheme in ("file", "data", "blob", "about", "") or (parts.hostname or "").lower() in snapshot.LOOPBACK


def _console(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The console's errors and warnings of one opening, each as (kind, words): «error», «warning», «internet» (a
    file of the internet not loaded - the check cuts it off)."""
    out = []
    for event in events:
        method, params = event.get("method"), event.get("params") or {}
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            said = ((details.get("exception") or {}).get("description") or details.get("text") or "").strip()
            said = said.splitlines()[0] if said else "an error"
            place = f" - {_file(details.get('url', ''))}:{int(details.get('lineNumber') or 0) + 1}" if details.get(
                "url") else ""  # fmt: skip
            out.append(("error", f"script error: {said[:MESSAGE]}{place}"))
        elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert", "warning"):
            words = " ".join(str(a.get("value") if "value" in a else a.get("description", a.get("type", "")))
                             for a in params.get("args") or [])  # fmt: skip
            frames = (params.get("stackTrace") or {}).get("callFrames") or []
            place = f" - {_file(frames[0]['url'])}:{int(frames[0].get('lineNumber') or 0) + 1}" if frames and frames[
                0].get("url") else ""  # fmt: skip
            kind = "warning" if params["type"] == "warning" else "error"
            out.append((kind, f"console.{'warn' if kind == 'warning' else 'error'}: {words[:MESSAGE]}{place}"))
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            if entry.get("level") not in ("error", "warning"):
                continue
            text, url = str(entry.get("text") or ""), str(entry.get("url") or "")
            if entry.get("source") == "network" and url:
                if not _local(url):
                    out.append(("internet", url[:200]))
                    continue
                why = text.removeprefix("Failed to load resource:").strip()
                why = {"net::ERR_FILE_NOT_FOUND": "no such file"}.get(why, why)
                out.append(("error", f"not loaded: {_file(url)} ({why[:120]})"))
                continue
            kind = "warning" if entry["level"] == "warning" else "error"
            out.append((kind, f"{entry.get('source', 'browser')}: {text[:MESSAGE]}"))
    return out


def _dropped(tools: _DevTools, session: str, styles: list[list[str]]) -> list[str]:
    """What of the page's CSS the browser dropped: its style sheets (as the page loaded them) and style attributes,
    weighed in the page itself."""
    items: list[Item] = []
    for event in tools.events:
        header = (event.get("params") or {}).get("header") or {}
        if event.get("method") != "CSS.styleSheetAdded" or header.get("origin") != "regular":
            continue
        with contextlib.suppress(SnapshotError):
            text = tools.call("CSS.getStyleSheetText", {"styleSheetId": header["styleSheetId"]}, session).get("text")
            if text:
                name = _file(header.get("sourceURL", "")) + (" <style>" if header.get("isInline") else "")
                items += css_items(text, name, int(header.get("startLine") or 0) + 1)
    for element, style in styles:
        items += css_items(str(style or ""), f"the style attribute of `{element}`:", inside="")
    items = items[:ITEMS]
    asked = [{"s": it.text} if it.kind == "rule" else {"p": it.prop, "v": it.value} for it in items
             if it.kind in ("rule", "decl")]  # fmt: skip
    verdicts = tools.value(f"({WEIGH_JS})({json.dumps(asked, ensure_ascii=False)})", session) if asked else []
    weighed = iter(verdicts or [])
    out = []
    for it in items:
        if it.kind == "at":
            out.append(f"{it.where} `{it.text}` - the browser does not know this at-rule: dropped (a word of a build "
                       "step, like Tailwind's, needs that step)")  # fmt: skip
        elif it.kind == "stray":
            out.append(f"{it.where} `{it.text}` - not a declaration in a rule: dropped")
        elif (verdict := next(weighed, 0)) == 1:
            out.append(f"{it.where} `{it.text}` - the browser cannot read this selector: the whole rule is dropped")
        elif verdict in (2, 3):
            said = "the browser does not take this value" if verdict == 2 else "there is no such property"
            out.append(f"{it.where} `{it.text}`" + (f" in `{it.rule}`" if it.rule else "") + f" - {said}: dropped")
    return out


def report(found: dict[str, Any], shown: str, *, picture: bool) -> str:
    """The check in words for the model: each kind of problem, the widths where it is; «nothing found» when the
    page is clean."""
    widths = [w.width for w in found["widths"]]
    every = len(widths)
    head = f"Page check of {shown} at {', '.join(map(str, widths))} px wide (window height {HEIGHT})."
    parts = [head]

    def listed(title: str, lines: list[str]) -> None:
        if lines:
            more = f"\n- … and {len(lines) - SHOWN} more" if len(lines) > SHOWN else ""
            parts.append(f"{title} ({len(lines)}):\n" + "\n".join(f"- {x}" for x in lines[:SHOWN]) + more)

    def at(seen: list[int]) -> str:
        return "" if len(seen) == every else f" (at {', '.join(map(str, seen))} px)"

    console = found["console"]
    listed("Console errors", [f"{w}{at(s)}" for (k, w), s in console.items() if k == "error"])
    listed("Console warnings", [f"{w}{at(s)}" for (k, w), s in console.items() if k == "warning"])
    far = [w for (k, w) in console if k == "internet"]
    if far:
        parts.append("Not loaded from the internet - the check cuts the internet off, this is not an error of the "
                     "page itself: " + ", ".join(far[:SHOWN]) + (" …" if len(far) > SHOWN else ""))  # fmt: skip
    listed("CSS the browser dropped", found["css"])
    clean = []
    for w in found["widths"]:
        lay, lines = w.layout, []
        if not w.loaded:
            lines.append(f"the page did not finish loading in {LOAD_S:.0f} s: checked as it was")
        inner, laid = int(lay.get("width") or w.width), int(lay.get("laid") or w.width)
        if w.width < MOBILE and not lay.get("viewport") and laid > w.width + 1:
            lines.append(f'there is no <meta name="viewport">: a phone lays the page out {laid} px wide and shrinks '
                         "it")  # fmt: skip
        elif w.width < MOBILE and laid > w.width + 1:
            lines.append(f"the page is {laid} px wide, wider than the phone's screen: a phone shows it zoomed out "
                         "(small text) or scrolls it sideways")  # fmt: skip
        elif int(lay.get("scroll") or 0) > inner + 1:
            then = "what is past the edge is cut off" if lay.get("cut") else "it scrolls sideways"
            lines.append(f"the page is {lay['scroll']} px wide, the window {inner}: {then}")
        for p in (lay.get("past") or [])[:SHOWN]:
            edge = "right" if p["right"] > inner + 1 else "left"
            lines.append(f"`{p['what']}` goes past the {edge} edge: from {p['left']} to {p['right']} px")
        for z in (lay.get("zero") or [])[:SHOWN]:
            lines.append(f"`{z['what']}` is {z['w']}x{z['h']} px - zero size: what it holds is not seen")
        for b in (lay.get("broken") or [])[:SHOWN]:
            lines.append(f"`{b}` - the picture is not shown: it did not load")
        if lines:
            parts.append(f"At {w.width} px:\n" + "\n".join(f"- {x}" for x in lines))
        else:
            clean.append(w.width)
    if len(parts) == 1:
        parts.append("Nothing found: no console errors, no CSS dropped, nothing past the edge or of zero size, every "
                     "picture shown.")  # fmt: skip
    elif clean:
        parts.append(f"Nothing past the edge or of zero size at {', '.join(map(str, clean))} px.")
    if picture:
        parts.append(f"The page's picture at {widths[0]}x{HEIGHT} comes to you after this result.")
    text = "\n".join(parts)
    return text if len(text) <= CHARS else text[: CHARS - 40] + "\n[… the rest of the check is cut]"
