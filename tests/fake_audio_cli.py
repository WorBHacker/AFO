"""A stand-in of audio.cpp's `audiocpp_cli` for the tests (stage 53): a real program started as the hub starts the
engine. It takes the engine's arguments and writes a real WAV of the kind the engine writes - a voice's line 24 kHz
mono, as long as its text read at 12 characters a second; music 48 kHz stereo and a sound effect 48 kHz mono, as
long as asked - a tone of its own pitch each, so a test can tell them in a mix. What it was asked goes, a line of
JSON each time, into the file $FAKE_AUDIO_LOG names. $FAKE_AUDIO_SLEEP - seconds it works before it writes;
$FAKE_AUDIO_FAIL - the family whose piece fails as the engine does, in words and with an exit code;
$FAKE_AUDIO_HIGH - so many of the voice's first readings (counted in the log) come a fifth up, as the engine's
voice that did not hold; $FAKE_AUDIO_LONG - so many go on to the end of their tokens (--max-tokens, 12.5 a second)."""

import json
import math
import os
import struct
import sys
import time
import wave

TONES = {"qwen3_tts": 220.0, "ace_step": 660.0, "moss_soundeffect": 1760.0}


def main() -> int:
    args = sys.argv[1:]

    def value(name: str, default: str = "") -> str:
        return args[args.index(name) + 1] if name in args else default

    options = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--request-option")
    family, text, out = value("--family"), value("--text"), value("--out")
    tone = TONES.get(family, 440.0)
    if family == "qwen3_tts":
        seconds, rate, channels = max(0.3, len(text) / 12.0), 24000, 1
        before = 0
        if (log := os.environ.get("FAKE_AUDIO_LOG")) and os.path.isfile(log):
            with open(log, encoding="utf-8") as f:
                before = sum(json.loads(line)["family"] == family for line in f)
        if before < int(os.environ.get("FAKE_AUDIO_HIGH") or 0):
            tone *= 1.5
        if before < int(os.environ.get("FAKE_AUDIO_LONG") or 0):
            seconds = int(value("--max-tokens", "2048")) / 12.5
    elif family == "ace_step":
        seconds, rate, channels = float(value("--duration-seconds", "10")), 48000, 2
    else:
        seconds, rate, channels = float(options.get("duration_sec", "10")), 48000, 1
    if log := os.environ.get("FAKE_AUDIO_LOG"):
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"family": family, "text": text, "model": value("--model"), "seconds": seconds,
                                "backend": value("--backend"), "seed": value("--seed"),
                                "voice": os.path.splitext(os.path.basename(value("--voice-ref")))[0],
                                "alone": options.get("x_vector_only_mode", ""),
                                "language": value("--language"), "lyrics": value("--lyrics"),
                                "cwd": os.getcwd()}, ensure_ascii=False) + "\n")  # fmt: skip
    if not os.path.isfile(value("--model")):
        print("error: model file not found")
        return 2
    until = time.monotonic() + float(os.environ.get("FAKE_AUDIO_SLEEP") or 0)
    while time.monotonic() < until:
        time.sleep(0.05)
    if os.environ.get("FAKE_AUDIO_FAIL") == family:
        print("ggml_cuda: CUDA error: out of memory")
        return 1
    frames = int(seconds * rate)
    step = 2 * math.pi * tone / rate
    with wave.open(out, "wb") as sound:
        sound.setnchannels(channels)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(step * n))) * channels for n in range(frames)))
    print("metrics.wall_ms=12.5")
    print(f"metrics.audio_duration_ms={seconds * 1000:.0f}")
    print(f"metrics.sample_rate={rate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
