"""Who holds the graphics card (stage 48's review, finding 2): one at a time on it - a job of the Studio (a picture or a
video) or an install's trial - and whether local chats wait for it too.

A holder takes the card in one step with the checks before it: `try_take` has no `await`, so nothing else runs on the
hub's loop between a check (tuning measures? a chat answers?) and the take - an install's trial waited for the card
once and a Studio's video came in between (the review: «между `_card` и записью конфига задание Студии успевает
начаться»). The others wait until it is given (`take`). Held from chats (`chats`), a local chat model's
answer waits at `gate` - llama-swap would swap the holder's model out under it; a holder whose own requests go through
that gate (a chat model's trial) does not hold the card from chats, or it would wait for itself.

After 48.8 this becomes the card's one owner - chat, tuning, trial, Studio, «Выгрузить» (48.9).

The local requests (48.9а) - whoever asks a model on the card, waiting at the gate too - are counted here, with who
asks: tuning and an install's trial wait for them (a cloud chat is not one). The llama-swap provider counts each of its
requests; a local chat model's turn counts from its start to its end (between its requests its model stays for the
next), the editor's completion as a whole (its fill in the middle goes through llama-swap's /upstream, past the gate).
Who asks is named in the context (`ASKER`): a turn, a completion, a request of the hub's API sets it for what it
does - the tasks it starts too.

The order of seniority (48.9б1, the user's word): the person now (a chat's message, the Studio, their buttons) > their
work without them (an agent, a schedule, the editor's completion, an outside client) > an install's trial > tuning. A
holder says how it is stopped (`stop`); a senior that needs the card - taking it, or asking at the gate - stops a
junior holding it and waits until it is given. The interrupted is not failed and keeps nothing: tuning measures again
later. Tuning holds the card (TUNE) from local requests too - llama-bench and llama-server run past llama-swap.

One rule of quiet for background work (48.9б2) - automatic tuning, a stopped one, a deferred trial: the card has been
still for QUIET_S (`Quiet`) - no local request, nobody holding it, its load under BUSY_PCT, no game on it even at low
load (in its menu, paused). A download is not on the card. Started by the person's button, it goes once the card is
still (`still`); stopped by a senior, it is background work - else it would come back at each message, and the models
go to and fro."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar

from common.i18n import N_, tr

STUDIO = "studio"  # the Studio's job: a picture or a video
TRIAL = "trial"  # an install's trial - and its requests (48.9а)
TUNE = "tune"  # tuning's measuring and its check (48.9б1)
# who asks a local model (48.9а)
CHAT = "chat"  # a person's message (the window, Telegram) and its answer with tools
BUTTON = "button"  # a person's button: «Проверить», a settings' trial, «Набросать карточку», a goal's draft, Ctrl+I
AGENT = "agent"  # an agent's run, an autonomous run, a goal's turn, a task on a schedule
COMPLETION = "completion"  # the editor's grey text
OPENAI = "openai"  # an outside client through /v1/openai
ASKER: ContextVar[str] = ContextVar("asker", default=AGENT)  # nobody named: a person's unattended work
QUIET_S = 180.0  # background work waits for the card still this long (48.9б2): time to think of the next message
BUSY_PCT = 15  # the card at this much load or more works for someone - a game, another program
# each holder's and asker's rank: 1 the person now, 2 their work without them, 3 an install's trial, 4 tuning (48.9б1)
RANKS = {CHAT: 1, BUTTON: 1, STUDIO: 1, AGENT: 2, COMPLETION: 2, OPENAI: 2, TRIAL: 3, TUNE: 4}
# why a local chat waits, in words - who holds the card from it: the Studio only for a video, a trial of a picture or
# video model (2б), tuning while it lets go (48.9б1)
WORDS = {STUDIO: N_("видеокарта снимает видео — ответ начнётся после него"),
         TRIAL: N_("на видеокарте пробный запуск новой модели — ответ начнётся после него"),
         TUNE: N_("видеокарта останавливает подбор параметров — ответ начнётся сразу после")}  # fmt: skip


def senior(who: str, than: str) -> bool:
    """Whether `who` comes before `than` on the card; one not ranked - as a person's unattended work."""
    return RANKS.get(who, RANKS[AGENT]) < RANKS.get(than, RANKS[AGENT])


@contextlib.contextmanager
def named(who: str) -> Iterator[None]:
    """The local requests made in this scope - and in the tasks started in it - are `who`'s. In a coroutine, not an
    async generator: its end may come in another context."""
    token = ASKER.set(who)
    try:
        yield
    finally:
        ASKER.reset(token)


def ask_as(who: str) -> None:
    """The local requests of this request to the hub's API are `who`'s: its task is its own, the answer's stream
    too (it starts from it)."""
    ASKER.set(who)


class GpuCard:
    def __init__(self) -> None:
        self.holder = ""  # who holds the card now - STUDIO, TRIAL, TUNE - or nobody
        self.free = asyncio.Event()  # local chats go on: the card is not held from them
        self.free.set()
        self._given = asyncio.Event()  # the card was let go: those waiting for it look again
        self._given.set()
        self._stop: Callable[[], None] | None = None  # how the holder is stopped by a senior (48.9б1)
        self.asked: dict[str, int] = {}  # who asks the local models now -> how many (48.9а)
        # set by the server (48.9б2): the card's load now, % - the status monitor's look; a game on it - 43.6's
        self.load: Callable[[], float | None] = lambda: None
        self.game: Callable[[], bool] = lambda: False

    def still(self) -> bool:
        """Nothing on the card now (48.9б2): no local request, nobody holds it, its load under BUSY_PCT, no game on
        it - one in its menu or paused loads it little and comes back any moment."""
        load = self.load()
        return not self.asked and not self.holder and (load is None or load < BUSY_PCT) and not self.game()

    @contextlib.contextmanager
    def asking(self, who: str | None = None) -> Iterator[None]:
        """A local request - or a turn, a completion - goes on in this scope: counted as `who`'s (by default, whose
        the context says)."""
        who = who or ASKER.get()
        self.asked[who] = self.asked.get(who, 0) + 1
        try:
            yield
        finally:
            if (left := self.asked.get(who, 1) - 1) > 0:
                self.asked[who] = left
            else:
                self.asked.pop(who, None)

    def try_take(self, holder: str, *, chats: bool = False, stop: Callable[[], None] | None = None) -> bool:
        """The card for `holder` if nobody holds it - at once, with no `await` between the look and the take; `chats` -
        local chat models wait for it too; `stop` - how a senior stops it (48.9б1; said again, it changes nothing). A
        junior holding the card is stopped here - the caller looks again once it is given."""
        if self.holder:
            self._stop_for(holder)
            return False
        self.holder, self._stop = holder, stop
        self._given.clear()
        if chats:
            self.free.clear()
        return True

    async def take(self, holder: str, *, chats: bool = False, stop: Callable[[], None] | None = None) -> None:
        """The card for `holder`, once the one holding it lets it go - a junior is stopped first."""
        while not self.try_take(holder, chats=chats, stop=stop):
            await self._given.wait()

    def give(self, holder: str) -> None:
        """`holder` lets the card go; one that does not hold it changes nothing."""
        if self.holder != holder:
            return
        self.holder, self._stop = "", None
        self.free.set()
        self._given.set()

    def _stop_for(self, who: str) -> None:
        """`who` needs the card: a junior holding it is stopped - and gives it once it has let go (48.9б1)."""
        if self.holder and self._stop is not None and senior(who, self.holder):
            self._stop()

    def words(self) -> str:
        """What a local chat waiting at the gate is told: who holds the card from it."""
        return tr(WORDS[self.holder]) if self.holder in WORDS and not self.free.is_set() else ""

    async def gate(self, cancel: asyncio.Event) -> bool:
        """A local chat model's answer waits here while the card is held from chats (the llama-swap provider asks
        before each); whether it waited. A junior holding it - tuning - is stopped first (48.9б1). The user's «Стоп»
        ends the wait."""
        if self.free.is_set():
            return False
        self._stop_for(ASKER.get())
        stopping = asyncio.ensure_future(cancel.wait())
        free = asyncio.ensure_future(self.free.wait())
        await asyncio.wait({stopping, free}, return_when=asyncio.FIRST_COMPLETED)
        for each in (stopping, free):
            each.cancel()
        return True


class Quiet:
    """Whether the card has been still for QUIET_S, as one waiter looks at it - each look, every few seconds (48.9б2).
    Counted from the waiter's start: a stopped one does not come back at once."""

    def __init__(self, card: GpuCard) -> None:
        self._card = card
        self._since = time.monotonic()

    def look(self) -> bool:
        now = time.monotonic()
        if not self._card.still():
            self._since = now
        return now - self._since >= QUIET_S
