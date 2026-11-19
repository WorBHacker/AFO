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
later, an install's trial is «ждёт пробного запуска» again (48.9в), both as background work. Tuning holds the card
(TUNE) from local requests too - llama-bench and llama-server run past llama-swap.

One rule of quiet for background work (48.9б2) - automatic tuning, a stopped one, a deferred trial: the card has been
still for QUIET_S (`Quiet`) - no local request, nobody holding it, its load under BUSY_PCT, no game on it even at low
load (in its menu, paused). A download is not on the card. Started by the person's button, it goes once the card is
still (`still`); stopped by a senior, it is background work - else it would come back at each message, and the models
go to and fro. A game begun while tuning or a trial goes on does not stop it, but what it found beside the game is not
kept (48.9б4, `Played`): it is stopped work - nothing written, back in the queue as background work.

«Выгрузить» goes through the owner (48.9г): «the card is needed by me now». Whoever holds it is stopped - the Studio's
job as by ✕, a trial put off, tuning stopped - and the card is given to the person (`give_away`): until their next
action of rank 1 (`take_back` - a chat's message, the Studio, their buttons) nobody takes it - background work, and the
person's work without them neither: at the gate it waits, with words. A hub restarted forgets it. Removing a model
stops the holder working with it (`let_go`), the card not given. «Продолжить» (48.9д, `go_on`) gives it back: what
waited goes on at once, in the order of seniority - the Studio's queue, the person's work without them, then background
work, which does not wait QUIET_S then: the card still is enough."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar

from common.i18n import N_, tr

STUDIO = "studio"  # the Studio's job: a picture or a video
SOUND = "sound"  # a film's added sound (53.2): the voice, music, a scene's sounds - audio.cpp, past llama-swap
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
RANKS = {CHAT: 1, BUTTON: 1, STUDIO: 1, SOUND: 1, AGENT: 2, COMPLETION: 2, OPENAI: 2, TRIAL: 3, TUNE: 4}
GIVEN_WORDS = N_("видеокарта отдана — „Продолжить“")  # what waits while the card is the person's (48.9г)
# why a local chat waits, in words - who holds the card from it: the Studio only for a video; a trial of a picture or
# video model (2б) and tuning while they let go (48.9в, 48.9б1)
WORDS = {STUDIO: N_("видеокарта снимает видео — ответ начнётся после него"),
         SOUND: N_("видеокарта озвучивает фильм — ответ начнётся после этого"),
         TRIAL: N_("видеокарта откладывает пробный запуск новой модели — ответ начнётся сразу после"),
         TUNE: N_("видеокарта останавливает подбор параметров — ответ начнётся сразу после")}  # fmt: skip


def rank(who: str) -> int:
    """`who`'s place on the card (48.9б1); one not ranked - as a person's unattended work."""
    return RANKS.get(who, RANKS[AGENT])


def senior(who: str, than: str) -> bool:
    """Whether `who` comes before `than` on the card."""
    return rank(who) < rank(than)


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
        self.model = ""  # the model the holder works with, when it says (48.9г: a model removed stops it)
        self.note = ""  # the holder's own words for a chat that waits (50.7: a film, scene after scene); "" - WORDS
        self.given = False  # «Выгрузить» (48.9г): the person's until their next action
        self._back = asyncio.Event()  # not given: those waiting for the person's card look again
        self._back.set()
        self.pushed_at = 0.0  # when «Продолжить» was said (48.9д): background work begun before it goes once still
        self.on_change: Callable[[], None] | None = None  # set by the server: the window hears of «отдана» (48.9д)
        self.asked: dict[str, int] = {}  # who asks the local models now -> how many (48.9а)
        # set by the server (48.9б2): the card's load now, % - the status monitor's look; a game on it - 43.6's
        # (48.9б3: one Windows or 43.6 knows, or drawing now - not a launcher by its folder's name)
        self.load: Callable[[], float | None] = lambda: None
        self.game: Callable[[], bool] = lambda: False

    def still(self) -> bool:
        """Nothing on the card now (48.9б2): no local request, nobody holds it, its load under BUSY_PCT, no game on
        it - one in its menu or paused loads it little and comes back any moment."""
        load = self.load()
        return (not self.asked and not self.holder and not self.given and (load is None or load < BUSY_PCT)
                and not self.game())  # fmt: skip

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

    def try_take(self, holder: str, *, chats: bool = False, stop: Callable[[], None] | None = None,
                 model: str = "") -> bool:  # fmt: skip
        """The card for `holder` if nobody holds it - at once, with no `await` between the look and the take; `chats` -
        local chat models wait for it too; `stop` - how a senior stops it (48.9б1; said again, it changes nothing);
        `model` - the one it works with (48.9г). A junior holding the card is stopped here - the caller looks again
        once it is given. Given to the person, nobody takes it (48.9г): their own action takes it back first."""
        if self.given:
            return False
        if self.holder:
            self._stop_for(holder)
            return False
        self.holder, self._stop, self.model = holder, stop, model
        self._given.clear()
        if chats:
            self.free.clear()
        return True

    async def take(self, holder: str, *, chats: bool = False, stop: Callable[[], None] | None = None,
                   model: str = "") -> None:  # fmt: skip
        """The card for `holder`, once the one holding it lets it go - a junior is stopped first; given to the person
        - once they take it back."""
        while not self.try_take(holder, chats=chats, stop=stop, model=model):
            await (self._back.wait() if self.given else self._given.wait())

    def give(self, holder: str) -> None:
        """`holder` lets the card go; one that does not hold it changes nothing."""
        if self.holder != holder:
            return
        self.holder, self._stop, self.model, self.note = "", None, "", ""
        self.free.set()
        self._given.set()

    def give_away(self) -> None:
        """«Выгрузить» (48.9г): whoever holds the card is stopped - whatever its rank - and the card is the person's."""
        self.given = True
        self._back.clear()
        if self.holder and self._stop is not None:
            self._stop()
        self.changed()

    def take_back(self) -> None:
        """The person's own action (48.9г: rank 1): the card given to them is theirs to use - and everyone's again."""
        if self.given:
            self.given = False
            self._back.set()
            self.changed()

    def go_on(self) -> None:
        """«Продолжить» (48.9д): the card given back, and what waited goes on at once - background work too."""
        self.pushed_at = time.monotonic()
        self.take_back()

    def state(self) -> dict[str, bool]:
        return {"given": self.given}

    def changed(self) -> None:
        """«Отдана» or the Studio's queue held came or went: the window is told (48.9д)."""
        if self.on_change is not None:
            self.on_change()

    def let_go(self, model_id: str) -> None:
        """A model removed (48.9г): the holder working with it stops, as by «Выгрузить»; the card is not given."""
        if self.holder and self.model == model_id and self._stop is not None:
            self._stop()

    async def until_let_go(self, seconds: float) -> None:
        """Until nobody holds the card - `seconds` at most."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._given.wait(), seconds)

    def _stop_for(self, who: str) -> None:
        """`who` needs the card: a junior holding it is stopped - and gives it once it has let go (48.9б1)."""
        if self.holder and self._stop is not None and senior(who, self.holder):
            self._stop()

    def words(self) -> str:
        """What a local chat waiting at the gate is told: who holds the card from it - or that it is the person's
        (48.9г: their work without them waits)."""
        if self.given and rank(ASKER.get()) > 1:
            return tr(GIVEN_WORDS)
        if self.holder not in WORDS or self.free.is_set():
            return ""
        return self.note or tr(WORDS[self.holder])

    async def gate(self, cancel: asyncio.Event) -> bool:
        """A local chat model's answer waits here while the card is held from chats (the llama-swap provider asks
        before each); whether it waited. A junior holding it - tuning, an install's trial - is stopped first (48.9б1,
        48.9в), a chat model's trial too, which does not hold the card from chats: its question is let go and
        llama-swap's queue is the asker's. Given to the person (48.9г): their own request takes it back, their work
        without them waits until they do. The user's «Стоп» ends the wait."""
        who, waited = ASKER.get(), False
        if self.given and rank(who) == 1:
            self.take_back()
        if self.given:
            waited = True
            await _either(cancel, self._back)
            if cancel.is_set():
                return True
        self._stop_for(who)
        if self.free.is_set():
            return waited
        await _either(cancel, self.free)
        return True


async def _either(cancel: asyncio.Event, event: asyncio.Event) -> None:
    """Until `event` is set or the user stops."""
    stopping = asyncio.ensure_future(cancel.wait())
    waiting = asyncio.ensure_future(event.wait())
    await asyncio.wait({stopping, waiting}, return_when=asyncio.FIRST_COMPLETED)
    for each in (stopping, waiting):
        each.cancel()


class Quiet:
    """Whether the card has been still for QUIET_S, as one waiter looks at it - each look, every few seconds (48.9б2).
    Counted from the waiter's start: a stopped one does not come back at once."""

    def __init__(self, card: GpuCard) -> None:
        self._card = card
        self._since = self._began = time.monotonic()

    def look(self) -> bool:
        """Whether it goes now - once still after «Продолжить» said since it began (48.9д)."""
        now = time.monotonic()
        if not self._card.still():
            self._since = now
            return False
        return self._card.pushed_at >= self._began or now - self._since >= QUIET_S


class Played:
    """Whether a game was on the card while tuning or a trial had it (48.9б4): the work does not give way to a game
    begun meanwhile - the quiet is looked at before it starts -, but what it found then is not kept: its numbers, its
    verdict were the game's too. `look` - now, or once since it began; `watch` looks every few seconds beside the work
    (43.6 looks at the programs every CHECK_S: a game shorter than that may pass unseen)."""

    def __init__(self, card: GpuCard) -> None:
        self._card = card
        self.seen = False

    def look(self) -> bool:
        self.seen = self.seen or bool(self._card.game())
        return self.seen

    async def watch(self, every: float) -> None:
        while not self.look():
            await asyncio.sleep(every)
