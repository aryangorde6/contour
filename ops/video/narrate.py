"""Turn ops/video.md's narration into one WAV per cue, plus caption timings.

Two things make synthesised narration sound like a person rather than a
machine, and neither is the choice of engine.

The first is that sentences are synthesised one at a time and reassembled with
pauses chosen here, not left to the engine's own idea of a full stop. A reader
breathes longer between paragraphs than between sentences, so the gaps differ.

The second is that the text is rewritten to what a presenter would say out
loud -- "execute dot p y", not "execute dot pie" -- rather than left for the
engine to guess at backticks, --flags and tickers.

Synthesising per sentence also means we know exactly when each one starts, so
subtitles fall out of the same pass instead of being timed by hand afterwards.
The caption keeps the written form (`attribution.py --offline`); only the
spoken form is respelled.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent.parent
# Everything generated lands here; it is gitignored. Override with
# CONTOUR_VIDEO_BUILD to keep renders outside the working tree.
SP = Path(os.environ.get("CONTOUR_VIDEO_BUILD", REPO / "build/video"))
OUT = SP / "build" / "audio"
PY = os.environ.get("CONTOUR_PY", REPO / ".venv/bin/python")

# Kokoro is the default because it is markedly more natural than Piper on
# long-form narration; Piper stays as a fallback so the pipeline still runs on
# a machine that only has the smaller model.
VOICES = Path(os.environ.get(
    "CONTOUR_VOICES", Path.home() / ".local/share/contour-voices"))
KOKORO_MODEL = VOICES / "kokoro-v1.0.onnx"
KOKORO_BIN = VOICES / "voices-v1.0.bin"
VOICE = os.environ.get("CONTOUR_VOICE", "am_michael")
PIPER_VOICE = os.environ.get("CONTOUR_PIPER_VOICE", "en_US-ryan-high")

# How long the narrator waits. Between sentences of one thought, barely; between
# paragraphs, long enough that the next idea reads as a new one.
GAP_SENTENCE = 0.34
GAP_PARAGRAPH = 0.72
GAP_TAIL = 0.30

HEADER = re.compile(r"^### (\d+) · ([^—\n]+?)\s+—\s+(.*)$", re.M)
# A sentence ends at .?! only when what follows starts a new one.
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(“])")

# Said aloud, not written. Order matters: longest first.
SAY = [
    ("attribution.py --offline", "attribution dot p y, with the offline flag"),
    ("--replay", "replay"),
    ("execute.py", "execute dot p y"),
    ("mind.py", "mind dot p y"),
    ("loop.py", "loop dot p y"),
    ("vrp_ratio", "V R P ratio"),
    ("skew_z", "skew Z"),
    ("reconcile()", "reconcile"),
    ("client_order_id", "client order I D"),
    ("P&L", "P and L"),
    ("GLM-5", "G L M five"),
    ("SHA-256", "S H A two fifty six"),
    ("QQQ", "Q Q Q"),
    ("IWM", "I W M"),
    ("25-delta", "twenty-five delta"),
    ("1.30", "one point three zero"),
]


def displayable(text: str) -> str:
    """What the subtitle shows: prose, with identifiers left intact."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = text.replace("`", "").replace("⚠️", "")
    # The warning markers flag numbers to re-check before recording; removing
    # one mid-sentence would otherwise strand a space before the comma.
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def speakable(text: str) -> str:
    """What the engine is given: the same sentence, respelled for the ear."""
    text = text.replace("—", ", ").replace("–", ", ")
    for a, b in SAY:
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def sentences(para: str) -> list[str]:
    parts = [s.strip() for s in SENTENCE.split(para) if s.strip()]
    # A three-word fragment is nearly always an abbreviation mis-split, not a
    # sentence; glue it back rather than synthesising it on its own.
    merged: list[str] = []
    for s in parts:
        if merged and len(s.split()) < 3:
            merged[-1] += " " + s
        else:
            merged.append(s)
    return merged


def cues() -> list[dict]:
    src = (REPO / "ops/video.md").read_text(encoding="utf-8")
    marks = list(HEADER.finditer(src))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(src)
        nxt = re.compile(r"^## ", re.M).search(src, m.end(), end)
        body = src[m.end():nxt.start() if nxt else end]
        paras, cur = [], []
        for line in body.splitlines():
            if line.startswith("> "):
                cur.append(line[2:].strip())
            elif line.strip() in ("", ">") and cur:
                paras.append(" ".join(cur))
                cur = []
        if cur:
            paras.append(" ".join(cur))
        # Stage directions live in italic blockquotes; they are not spoken.
        paras = [p for p in paras if not (p.startswith("*") and p.endswith("*"))]
        spoken = [[displayable(s) for s in sentences(displayable(p))]
                  for p in paras if p.strip()]
        out.append({"n": int(m.group(1)), "title": m.group(2).strip(),
                    "paras": [p for p in spoken if p]})
    return out


class Kokoro:
    rate = 24000

    def __init__(self) -> None:
        from kokoro_onnx import Kokoro as _K
        self.k = _K(str(KOKORO_MODEL), str(KOKORO_BIN))

    def say(self, text: str) -> np.ndarray:
        samples, sr = self.k.create(text, voice=VOICE, speed=1.0,
                                    lang="en-us")
        assert sr == self.rate, sr
        return np.asarray(samples, dtype=np.float32)


class Piper:
    rate = 22050

    def say(self, text: str) -> np.ndarray:
        tmp = OUT / "_piper.wav"
        subprocess.run(
            [str(PY), "-m", "piper", "-m", PIPER_VOICE, "-f", str(tmp),
             "--length-scale", "1.04"],
            input=text, text=True, cwd=str(VOICES),
            capture_output=True, check=True)
        with wave.open(str(tmp)) as w:
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return raw.astype(np.float32) / 32768.0


def engine():
    if KOKORO_MODEL.exists() and KOKORO_BIN.exists():
        return Kokoro()
    print("  (kokoro model not found, falling back to piper)")
    return Piper()


def trim(a: np.ndarray, rate: int, floor: float = 0.006) -> np.ndarray:
    """Strip the engine's own leading/trailing padding so the pauses below are
    the only thing setting the pace."""
    if a.size == 0:
        return a
    win = max(1, rate // 200)
    env = np.abs(a[:len(a) // win * win].reshape(-1, win)).max(axis=1)
    loud = np.flatnonzero(env > floor)
    if loud.size == 0:
        return a
    keep = max(1, rate // 40)                     # leave 25ms either side
    lo = max(0, loud[0] * win - keep)
    hi = min(len(a), (loud[-1] + 1) * win + keep)
    return a[lo:hi]


def write_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def master(src: Path, dest: Path) -> float:
    """Give the raw synthesis the polish a recorded voice-over would get.

    Every filter here preserves length -- nothing is trimmed or time-stretched
    -- so the caption times computed above stay valid.
    """
    import imageio_ffmpeg
    chain = ("highpass=f=75,"
             "acompressor=threshold=-18dB:ratio=3:attack=12:release=180,"
             "loudnorm=I=-16:TP=-1.5:LRA=11,"
             "alimiter=limit=0.94,"
             "aresample=48000")
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
         "-i", str(src), "-af", chain, "-ar", "48000", "-ac", "1",
         str(dest)], check=True, capture_output=True)
    with wave.open(str(dest)) as w:
        return w.getnframes() / w.getframerate()


# Words that lean on the one after them; a card that ends here reads as cut
# off rather than paused. Numbers are the worst offender -- "plus / two-thirty"
# splits one quantity across two cards.
STICKY = frozenset("""
a an the of to in on at for from and or but with as by than that this these
those its it's my our your is was are were be been plus minus over under about
one two three four five six seven eight nine ten twenty thirty hundred
""".split())


def caption_chunks(text: str, limit: int = 88) -> list[str]:
    """Split a sentence across as many cards as it needs, balanced.

    Breaking at a comma or a dash puts the cut where a reader would pause
    anyway, so each card reads as a phrase rather than a truncation. Cards are
    sized evenly instead of greedily, which avoids leaving a three-word stub
    alone on the last one.
    """
    text = text.strip()
    if len(text) <= limit:
        return [text]
    cards = -(-len(text) // limit)                 # ceil: fewest cards that fit
    target = len(text) / cards
    words = text.split()
    chunks, cur = [], ""
    for i, word in enumerate(words):
        nxt = f"{cur} {word}".strip()
        remaining = len(words) - i - 1
        # Close the card once it is near its share, preferring to stop just
        # after punctuation, and never overflowing the hard limit.
        tail = cur.rsplit(" ", 1)[-1].strip(",;:.—-").lower()
        # Punctuation is the best place to stop; a sticky word is the worst.
        breakable = (cur and cur[-1] in ",;:—-") or len(nxt) >= target
        if tail in STICKY:
            breakable = False
        if cur and (len(nxt) > limit or
                    (breakable and len(cur) >= target * 0.72 and
                     len(chunks) < cards - 1 and remaining)):
            head, carry = cur, word
            # The hard limit can force a break the rule above would refuse;
            # push the trailing sticky words onto the next card instead.
            while " " in head and head.rsplit(" ", 1)[-1].lower() in STICKY:
                head, moved = head.rsplit(" ", 1)
                carry = f"{moved} {carry}"
            chunks.append(head)
            cur = carry
        else:
            cur = nxt
    if cur:
        chunks.append(cur)
    return chunks


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tts = engine()
    print(f"  engine {type(tts).__name__}  voice "
          f"{VOICE if isinstance(tts, Kokoro) else PIPER_VOICE}")
    rate = tts.rate
    manifest: dict[str, list[dict]] = {}
    total = 0.0

    for cue in cues():
        pieces: list[np.ndarray] = []
        marks: list[tuple[float, float, str]] = []
        cursor = 0.0
        for pi, para in enumerate(cue["paras"]):
            if pi:
                pieces.append(np.zeros(int(GAP_PARAGRAPH * rate), np.float32))
                cursor += GAP_PARAGRAPH
            for si, sentence in enumerate(para):
                if si:
                    pieces.append(np.zeros(int(GAP_SENTENCE * rate),
                                           np.float32))
                    cursor += GAP_SENTENCE
                audio = trim(tts.say(speakable(sentence)), rate)
                secs = len(audio) / rate
                pieces.append(audio)
                marks.append((cursor, cursor + secs, sentence))
                cursor += secs
        pieces.append(np.zeros(int(GAP_TAIL * rate), np.float32))
        cursor += GAP_TAIL

        raw = OUT / f"cue{cue['n']:02d}-raw.wav"
        dest = OUT / f"cue{cue['n']:02d}.wav"
        write_wav(raw, np.concatenate(pieces), rate)
        actual = master(raw, dest)
        raw.unlink()
        # Mastering is length-preserving, but resampling can round by a frame
        # or two; nudge the captions so they cannot drift.
        scale = actual / cursor if cursor else 1.0

        rows = []
        for start, end, sentence in marks:
            chunks = caption_chunks(sentence)
            span = (end - start) / max(1, sum(len(c) for c in chunks))
            at = start
            for chunk in chunks:
                width = len(chunk) * span
                rows.append({"start": round(at * scale, 3),
                             "end": round((at + width) * scale, 3),
                             "text": chunk})
                at += width
        manifest[str(cue["n"])] = rows
        total += actual
        words = sum(len(s.split()) for p in cue["paras"] for s in p)
        print(f"  {cue['n']}  {actual:6.2f}s  {words:4d}w  "
              f"{len(rows):3d} caption(s)  {cue['title']}")

    (OUT / "timing.json").write_text(json.dumps(manifest, indent=1),
                                     encoding="utf-8")
    print(f"\nnarration total {int(total)//60}:{int(total)%60:02d} "
          f"({total:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
