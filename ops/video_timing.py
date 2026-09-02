"""Re-derive every word count and timing in ops/video.md from the narration.

The script's section headers carry a word count and a cumulative time range.
Both were hand-estimated once and both were wrong by about forty percent -- a
"4:10" script that actually ran 5:03 -- because a narration paragraph reads
much shorter than it counts. Estimating them again by eye would reproduce the
same error, so they are computed from the blockquotes that hold the narration.

    python ops/video_timing.py            # rewrite the headers and the table
    python ops/video_timing.py --check    # non-zero if anything is stale

Only lines beginning "> " count. Stage directions in italics, notes to the
reader and the cut table are not spoken, so they are not timed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / "ops/video.md"

WPM = 145                      # the planned pace; the header table shows others
PACES = ((155, "brisk"), (WPM, "planned"), (135, "unhurried"), (125, "slow"))

HEADER = re.compile(r"^### (\d+) · ([^—\n]+?)\s+—\s+(.*)$", re.M)


def mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(round(seconds)) % 60:02d}"


def sections(text: str) -> list[dict]:
    """Each ### section with the word count of its narration blockquotes."""
    out = []
    marks = list(HEADER.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        # The LAST section must not run to EOF: the deck notes below the script
        # contain blockquotes that are guidance, not narration, and counting
        # them added 42 phantom words to the close.
        nxt = re.compile(r"^## ", re.M).search(text, m.end(), end)
        body = text[m.end():nxt.start() if nxt else end]
        words = sum(len(line[2:].split())
                    for line in body.splitlines() if line.startswith("> "))
        # The tail of the header line is "<timings> · *cue* · <n>w" or, once a
        # section has been edited by hand, just "*cue*". Keep the cue only.
        tail = m.group(3)
        parts = [p.strip() for p in tail.split(" · ")]
        cue = next((p for p in parts if p.startswith("*")), tail)
        out.append({"n": m.group(1), "title": m.group(2).strip(),
                    "cue": cue, "words": words, "span": m.span()})
    return out


def rewrite(text: str) -> tuple[str, int]:
    secs = sections(text)
    total = sum(s["words"] for s in secs)

    # Cumulative timings, at the planned pace.
    at = 0.0
    for s in secs:
        start, at = at, at + s["words"] / WPM * 60.0
        s["range"] = f"{mmss(start)}–{mmss(at)}"

    for s in reversed(secs):                       # reverse: spans stay valid
        line = (f"### {s['n']} · {s['title']} — {s['range']} · {s['cue']} · "
                f"{s['words']}w")
        text = text[:s["span"][0]] + line + text[s["span"][1]:]

    text = re.sub(r"Narration is \*\*\d+ words\*\*",
                  f"Narration is **{total} words**", text)
    table = "\n".join(
        f"| {'**' if p == WPM else ''}{p} wpm ({label})"
        f"{'**' if p == WPM else ''} | "
        f"{'**' if p == WPM else ''}{mmss(total / p * 60)}"
        f"{'**' if p == WPM else ''} |"
        for p, label in PACES)
    text = re.sub(r"(\| Pace \| Runtime \|\n\|---\|---\|\n)(?:\|.*\|\n)+",
                  lambda m: m.group(1) + table + "\n", text)

    # The cut table's "Saves" column is hand-measured (it describes specific
    # paragraphs) but its running total and timings are arithmetic, and they
    # were left behind by every edit to the script above them.
    def cuts(m: re.Match) -> str:
        running = total
        rows = []
        for line in m.group(2).strip().splitlines():
            cells = [c.strip() for c in line.strip("|").split("|")]
            saved = int(re.search(r"\d+", cells[1]).group())
            running -= saved
            rows.append(f"| {cells[0]} | \u2212{saved}w | **{running}w** | "
                        f"{mmss(running / 145 * 60)} | {mmss(running / 135 * 60)} |")
        return m.group(1) + "\n".join(rows) + "\n"

    text = re.sub(r"(\| Cut \| Saves \| Script \| @145 \| @135 \|\n"
                  r"\|---\|---:\|---:\|---:\|---:\|\n)((?:\|.*\|\n)+)",
                  cuts, text)
    return text, total, secs


def main(argv: list[str]) -> int:
    before = VIDEO.read_text(encoding="utf-8")
    after, total, secs = rewrite(before)
    if "--check" in argv:
        if before != after:
            print("ops/video.md timings are stale; run ops/video_timing.py")
            return 1
        print(f"ops/video.md: {total} words, {mmss(total / WPM * 60)} at {WPM} wpm")
        return 0
    VIDEO.write_text(after, encoding="utf-8")
    print(f"{total} narration words -> {mmss(total / WPM * 60)} at {WPM} wpm")
    for s in secs:
        print(f"  {s['n']}  {s['range']:>12}  {s['words']:>4}w  {s['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
