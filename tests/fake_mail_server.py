"""A mailbox for the tests of the agent's mail (stage 9.4b): a small IMAP server (LOGIN, EXAMINE, UID SEARCH, UID
FETCH with BODY.PEEK - nothing gets marked read) and an SMTP server (EHLO, AUTH PLAIN, MAIL, RCPT, DATA) on
127.0.0.1, plain TCP, in threads. Python's own imaplib and smtplib talk to them as to a real mailbox."""

from __future__ import annotations

import base64
import email
import email.policy
import re
import socketserver
import threading
from typing import Any

USER, PASSWORD = "anna@example.com", "app-password-1"


class Box:
    """The letters (uid, raw, seen) and what was sent."""

    def __init__(self) -> None:
        self.letters: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []

    def add(self, raw: bytes, seen: bool = False) -> int:
        uid = len(self.letters) + 1
        self.letters.append({"uid": uid, "raw": raw, "seen": seen})
        return uid


def _uids(spec: str, top: int) -> list[int]:
    out = []
    for part in spec.split(","):
        if ":" in part:
            a, b = part.split(":")
            low, high = int(a), top if b == "*" else int(b)
            out += range(low, high + 1)
        elif part:
            out.append(int(part))
    return out


class _Imap(socketserver.StreamRequestHandler):
    box: Box

    def say(self, text: str | bytes) -> None:
        self.wfile.write(text if isinstance(text, bytes) else text.encode())

    def handle(self) -> None:
        self.say("* OK fake IMAP4rev1 ready\r\n")
        selected = False
        while line := self.rfile.readline():
            tag, _, rest = line.decode().rstrip("\r\n").partition(" ")
            command, _, args = rest.partition(" ")
            command = command.upper()
            if command == "CAPABILITY":
                self.say(f"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n{tag} OK done\r\n")
            elif command == "LOGIN":
                user, password = [a.strip('"') for a in args.split(" ", 1)]
                ok = user == USER and password == PASSWORD
                self.say(f"{tag} OK logged in\r\n" if ok else f"{tag} NO [AUTHENTICATIONFAILED] wrong password\r\n")
            elif command in ("SELECT", "EXAMINE"):
                if args.strip('"').upper() != "INBOX":
                    self.say(f"{tag} NO no such mailbox\r\n")
                    continue
                selected = True
                self.say(
                    f"* {len(self.box.letters)} EXISTS\r\n* OK [UIDVALIDITY 1] ok\r\n{tag} OK [READ-ONLY] done\r\n"
                )
            elif command == "UID" and selected:
                self.uid(tag, args)
            elif command == "LOGOUT":
                self.say(f"* BYE\r\n{tag} OK bye\r\n")
                return
            else:
                self.say(f"{tag} OK\r\n" if command == "NOOP" else f"{tag} BAD {command}\r\n")

    def uid(self, tag: str, args: str) -> None:
        sub, _, rest = args.partition(" ")
        letters = self.box.letters
        top = max((m["uid"] for m in letters), default=0)
        if sub.upper() == "SEARCH":
            what = rest.upper()
            if what == "UNSEEN":
                found = [m["uid"] for m in letters if not m["seen"]]
            elif what.startswith("UID "):
                wanted = set(_uids(what[4:], top))
                found = [m["uid"] for m in letters if m["uid"] in wanted]
            else:
                found = [m["uid"] for m in letters]
            self.say(f"* SEARCH {' '.join(map(str, found))}\r\n{tag} OK done\r\n")
            return
        spec, _, items = rest.partition(" ")
        wanted = set(_uids(spec, top))
        for seq, letter in enumerate(letters, 1):
            if letter["uid"] not in wanted:
                continue
            if "HEADER.FIELDS" in items.upper():
                message = email.message_from_bytes(letter["raw"], policy=email.policy.default)
                body = "".join(f"{h}: {message[h]}\r\n" for h in ("From", "Subject", "Date") if message[h]) + "\r\n"
                data, label = body.encode(), "BODY[HEADER.FIELDS (FROM SUBJECT DATE)]"
            else:
                data, label = letter["raw"], "BODY[]"
            flags = " FLAGS (\\Seen)" if letter["seen"] and "FLAGS" in items.upper() else (
                " FLAGS ()" if "FLAGS" in items.upper() else "")  # fmt: skip
            self.say(
                f"* {seq} FETCH (UID {letter['uid']}{flags} {label} {{{len(data)}}}\r\n".encode() + data + b")\r\n"
            )
        self.say(f"{tag} OK done\r\n")


class _Smtp(socketserver.StreamRequestHandler):
    box: Box

    def say(self, text: str) -> None:
        self.wfile.write(text.encode())

    def handle(self) -> None:
        self.say("220 fake ESMTP\r\n")
        envelope: dict[str, Any] = {"to": []}
        authed = False
        while line := self.rfile.readline():
            text = line.decode().rstrip("\r\n")
            verb = text.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self.say("250-fake\r\n250-AUTH PLAIN LOGIN\r\n250 OK\r\n")
            elif verb == "AUTH":
                parts = text.split(" ")
                user, password = base64.b64decode(parts[2]).decode().split("\0")[1:3]
                authed = user == USER and password == PASSWORD
                self.say("235 ok\r\n" if authed else "535 wrong password\r\n")
            elif verb == "MAIL":
                if not authed:
                    self.say("530 log in first\r\n")
                    continue
                envelope = {"from": re.search(r"<(.*)>", text)[1], "to": []}
                self.say("250 ok\r\n")
            elif verb == "RCPT":
                envelope["to"].append(re.search(r"<(.*)>", text)[1])
                self.say("250 ok\r\n")
            elif verb == "DATA":
                self.say("354 go on\r\n")
                lines = []
                while (data := self.rfile.readline()) not in (b".\r\n", b""):
                    lines.append(data[1:] if data.startswith(b"..") else data)
                raw = b"".join(lines)
                message = email.message_from_bytes(raw, policy=email.policy.default)
                self.box.sent.append({**envelope, "raw": raw, "message": message})
                self.say("250 queued\r\n")
            elif verb == "QUIT":
                self.say("221 bye\r\n")
                return
            else:
                self.say("250 ok\r\n")


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class MailServers:
    """Both servers on free ports; `box` is what they share. A context manager."""

    def __init__(self) -> None:
        self.box = Box()
        box = self.box
        self.imap = _Server(("127.0.0.1", 0), type("Imap", (_Imap,), {"box": box}))
        self.smtp = _Server(("127.0.0.1", 0), type("Smtp", (_Smtp,), {"box": box}))

    def account(self) -> dict[str, Any]:
        return {"address": USER, "imap_host": "127.0.0.1", "imap_port": self.imap.server_address[1],
                "imap_security": "plain", "smtp_host": "127.0.0.1", "smtp_port": self.smtp.server_address[1],
                "smtp_security": "plain"}  # fmt: skip

    def __enter__(self) -> MailServers:
        for server in (self.imap, self.smtp):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        for server in (self.imap, self.smtp):
            server.shutdown()
            server.server_close()


def letter(sender: str, subject: str, text: str, *, html: str = "", attach: str = "") -> bytes:
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = sender
    message["To"] = USER
    message["Subject"] = subject
    message["Date"] = "Tue, 27 Oct 2026 10:00:00 +0300"
    message["Message-ID"] = f"<{abs(hash(subject))}@example.org>"
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    if attach:
        message.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename=attach)
    return message.as_bytes()
