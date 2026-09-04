"""Turn ops/video.md's narration into one WAV per cue.

The script is written to be read by a person, so it is full of things a TTS
engine says wrong: backticked identifiers, `--flags`, tickers, and markdown
emphasis. Each is rewritten to what a presenter would actually say out loud --
"execute dot p y", not "execute dot pie" -- rather than left for the engine to
guess.
"""
from __future__ import annotations

import re
import subprocess
import sys
import wave
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
# Everything generated lands here; it is gitignored. Override with
# CONTOUR_VIDEO_BUILD to keep renders outside the working tree.
SP = Path(os.environ.get("CONTOUR_VIDEO_BUILD", REPO / "build/video"))
OUT = SP / "build" / "audio"
VOICE = "en_US-ryan-high"
PY = os.environ.get("CONTOUR_PY", REPO / ".venv/bin/python")

HEADER = re.compile(r"^### (\d+) · ([^—\n]+?)\s+—\s+(.*)$", re.M)

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
    ("t of", "t of"),
    ("contour", "contour"),
]


def speakable(text: str) -> str:
    text = text.replace("`", "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = text.replace("⚠️", "").replace("—", ", ").replace("–", ", ")
    for a, b in SAY:
        text = text.replace(a, b)
    text = re.sub(r"\s+", " ", text).strip()
    # A sentence that ends without punctuation runs into the next one.
    return text


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
        out.append({"n": int(m.group(1)), "title": m.group(2).strip(),
                    "paras": [speakable(p) for p in paras if p.strip()]})
    return out


def synth(text: str, dest: Path) -> float:
    subprocess.run(
        [str(PY), "-m", "piper", "-m", VOICE, "-f", str(dest),
         "--length-scale", "1.06", "--sentence-silence", "0.42"],
        input=text, text=True, cwd=str(SP / "voices"),
        capture_output=True, check=True)
    with wave.open(str(dest)) as w:
        return w.getnframes() / w.getframerate()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0.0
    rows = []
    for cue in cues():
        text = " ".join(cue["paras"])
        dest = OUT / f"cue{cue['n']:02d}.wav"
        secs = synth(text, dest)
        total += secs
        rows.append((cue["n"], cue["title"], secs, len(text.split())))
        print(f"  {cue['n']}  {secs:6.2f}s  {len(text.split()):4d}w  "
              f"{cue['title']}")
    print(f"\nnarration total {int(total)//60}:{int(total)%60:02d} "
          f"({total:.1f}s)")
    (OUT / "durations.txt").write_text(
        "\n".join(f"{n}\t{s:.3f}" for n, _, s, _ in rows) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
