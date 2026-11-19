"""A scene's sound heard and judged (stage 53.13). The listener (hub/sound.py, `listen`) names what sounds in it -
AudioSet's labels, each with how sure it is; ffmpeg measures where its energy lies (hub/ffmpeg.py, `bands`); the
director's model says whether what is heard is what the scene asks for, and names it in the film's language
(`question`, `reader` - asked through Judge.complete). `verdict` puts the three together: what is heard, why it is
not right ('' - it is), and a mark by which the takes of one sound are compared.

Why two measures (53.10, the user's ear: «дождь как из старой советской динамики»): the listener says *what*
sounds, not how - the video model's rain, with nothing above 8 kHz, was «Rain on surface» to it, and a hum under
800 Hz a «Thunderstorm» with rain. The numbers (53.12, the stand): of what lies above 100 Hz the video model's rain
had 0.010 and 0.022 above 8 kHz, the rain the engine of sound makes 0.04-0.12; a thunder has nothing above 1 kHz
whoever makes it - a thunder is so.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from common.i18n import tr

# AudioSet's names of what is a broad noise by its nature - rain, water, wind, steam: without its top it is not that
BROAD = frozenset({
    "Rain", "Raindrop", "Rain on surface", "Water", "Stream", "Waterfall", "Ocean", "Waves, surf", "Wind",
    "Wind noise (microphone)", "Rustling leaves", "Fire", "Crackle", "Steam", "Hiss", "Spray", "Pour",
    "Trickle, dribble", "Splash, splatter", "Drip", "Gurgling", "Slosh", "Gush", "Boiling", "Sizzle",
    "Frying (food)", "White noise", "Pink noise", "Static", "Applause", "Liquid", "Fill (with liquid)",
})  # fmt: skip
# ...and of what is low by its nature: nothing above 1 kHz in it is no fault
LOW = frozenset({
    "Thunder", "Thunderstorm", "Rumble", "Explosion", "Boom", "Engine", "Idling", "Engine starting",
    "Heavy engine (low frequency)", "Medium engine (mid frequency)", "Vehicle", "Car", "Truck", "Bus",
    "Motor vehicle (road)", "Traffic noise, roadway noise", "Train", "Rail transport", "Subway, metro, underground",
    "Aircraft", "Aircraft engine", "Fixed-wing aircraft, airplane", "Boat, Water vehicle", "Ship", "Motorcycle",
    "Heart sounds, heartbeat", "Heart murmur", "Bass drum", "Hum", "Mains hum",
})  # fmt: skip
SURE = 0.2  # a label the listener is at least this sure of is heard
AIR = 0.03  # a broad noise with less of its energy above 8 kHz sounds as from an old loudspeaker
MID = 0.05  # a sound with less above 1 kHz is a hum
TOLD = 5  # the labels shown to the director's model, and - when it does not answer - the first two to the user
HEARD_CHARS, WHY_CHARS = 80, 160

SYSTEM = """You check the sound of one scene of a short video. A sound classifier has listened to it and named \
what it hears: AudioSet labels, each with how sure it is, 0 to 1. You get what the scene shows, what it was asked \
to sound like, and the labels.

Does the sound fit? It fits when the main thing asked for is heard: one of the surer labels names it or its close \
kin (rain - rain on surface, raindrop, water; footsteps - walk, run, shuffle; a car - vehicle, engine, traffic \
noise). A classifier is never sure of everything: a second thing that was asked for and is not among the labels \
does not make the sound wrong. It does not fit when the surest labels name another thing than was asked for - \
music or speech where none was asked, an engine instead of rain - or silence.

Answer with the JSON object only: {form}"""  # i18n: keep
FORM = ('{"fits": true | false, "heard": "<what is heard: two to five words, in the language of what the scene '
        'shows>", "why": "<when it does not fit: one short phrase in that language - what was asked for and what is '
        'heard instead; else empty>"}')  # fmt: skip

Labels = list[tuple[str, float]]


def fault(labels: Labels, bands: dict[str, float] | None) -> str:
    """Why a sound is not right by its measure alone, in the user's words; '' - nothing is found."""
    if bands is None or (labels and labels[0][0] == "Silence"):
        return tr("тишина")
    first = labels[0][0] if labels else ""
    if any(name in BROAD and sure >= SURE for name, sure in labels) and bands["air"] < AIR:
        return tr("глухо, как из старого динамика: нет верхних частот")
    if bands["mid"] < MID and first not in LOW:
        return tr("глухой гул: выше 1 кГц ничего нет")
    return ""


def question(scene: dict[str, Any], labels: Labels) -> tuple[str, str, str]:
    """What the director's model is asked of a scene's sound: (system, prompt, form)."""
    listed = "\n".join(f"- {name}: {sure:.2f}" for name, sure in labels[:TOLD]) or "- nothing"
    prompt = (f"<scene>\nWhat it shows: {scene.get('words') or scene.get('prompt') or ''}\n"
              f"Asked to sound like: {scene['sound_words']}\n</scene>\n<heard>\n{listed}\n</heard>\n"
              "Does the sound fit? Answer with the JSON only.")  # fmt: skip
    return SYSTEM.format(form=FORM), prompt, FORM


def reader() -> Callable[[str], dict[str, Any] | None]:
    """What reads the answer: {fits, heard, why}; None - not a verdict, asked again."""

    def read(answer: str) -> dict[str, Any] | None:
        start, end = answer.find("{"), answer.rfind("}")
        try:
            data = json.loads(answer[start : end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("fits"), bool):
            return None
        return {"fits": data["fits"], "heard": " ".join(str(data.get("heard") or "").split())[:HEARD_CHARS],
                "why": " ".join(str(data.get("why") or "").split())[:WHY_CHARS]}  # fmt: skip

    return read


def verdict(labels: Labels, bands: dict[str, float] | None, said: dict[str, Any] | None) -> dict[str, Any]:
    """A sound's verdict: {heard - what sounds in it, in words; bad - why it is not right, '' - it is; mark - the
    higher the better: a take that fits is over one that does not, and of two alike the one without a fault of
    its measure}. `said` - the director's model's answer; None - it was not asked or did not answer: the listener's
    own labels are the words, and only the measure can find a fault."""
    measured = fault(labels, bands)
    heard = (said or {}).get("heard") or ", ".join(name for name, _sure in labels[:2]) or tr("тишина")
    wrong = said is not None and not said["fits"]
    bad = (said["why"] or tr("слышно не то, что в описании")) if wrong else measured
    fits = 1 if said is None else 2 if said["fits"] else 0
    return {"heard": heard, "bad": bad, "mark": 2 * fits + (not measured)}
