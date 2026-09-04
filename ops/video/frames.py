"""Terminal frames and title cards for the demo video.

The two strongest pieces of evidence in the submission are terminal output --
`--replay` running every gate, and the backtest that killed a feature. A still
screenshot of either reads as a claim; text arriving line by line reads as a
command that ran. So both are rendered as frame sequences and played back at
the pace the narration needs.

Nothing here invents output: replay.txt is the real run captured earlier, and
the backtest text is the committed research artefact.
"""
from __future__ import annotations

import html
import subprocess
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
# Everything generated lands here; it is gitignored. Override with
# CONTOUR_VIDEO_BUILD to keep renders outside the working tree.
SP = Path(os.environ.get("CONTOUR_VIDEO_BUILD", REPO / "build/video"))
BUILD = SP / "build"
FRAMES = BUILD / "frames"

BG, FG, DIM, OK, WARN, ACC = ("#0b0d10", "#d7dce3", "#7c8494",
                              "#46d18a", "#f2616b", "#7aa2f7")

SHELL = """
<style>
  html,body{margin:0;height:100%%;background:%(bg)s}
  body{font:%(size)spx/1.46 "DejaVu Sans Mono","Noto Sans Mono",monospace;
       color:%(fg)s;padding:44px 56px;box-sizing:border-box;overflow:hidden}
  .p{color:%(acc)s}
  pre{margin:0;white-space:pre}
  .ok{color:%(ok)s} .bad{color:%(warn)s} .dim{color:%(dim)s}
  .cursor{background:%(fg)s;color:%(bg)s}
</style>
<div class="p">$ %(cmd)s</div>
<pre>%(body)s</pre>
"""


def colorise(line: str) -> str:
    e = html.escape(line)
    if "[ok  ]" in line:
        return e.replace("[ok  ]", '<span class="ok">[ok  ]</span>')
    if "[VETO]" in line or "[veto]" in line:
        return (e.replace("[VETO]", '<span class="bad">[VETO]</span>')
                 .replace("[veto]", '<span class="bad">[veto]</span>'))
    if line.startswith("[replay]"):
        return f'<span class="dim">{e}</span>'
    if "NO_TRADE" in line or "refus" in line:
        return f'<span class="bad">{e}</span>'
    if "CONDOR" in line or "chain intact" in line:
        return f'<span class="ok">{e}</span>'
    return e


def render(name: str, cmd: str, lines: list[str], size: int = 19) -> Path:
    page = BUILD / f"term-{name}.html"
    page.write_text(SHELL % {"bg": BG, "fg": FG, "dim": DIM, "ok": OK,
                             "warn": WARN, "acc": ACC, "size": size,
                             "cmd": html.escape(cmd),
                             "body": "\n".join(colorise(l) for l in lines)},
                    encoding="utf-8")
    dest = FRAMES / f"{name}.png"
    subprocess.run(["google-chrome", "--headless", "--disable-gpu",
                    "--no-sandbox", "--hide-scrollbars",
                    "--window-size=1920,1080", "--virtual-time-budget=1200",
                    f"--screenshot={dest}", f"file://{page}"],
                   capture_output=True, check=False)
    return dest


def sequence(tag: str, cmd: str, lines: list[str], frames: int,
             window: int, size: int = 19) -> list[Path]:
    """Reveal `lines` over `frames` shots, scrolling once past `window`."""
    out = []
    for i in range(frames):
        shown = max(1, round(len(lines) * (i + 1) / frames))
        visible = lines[max(0, shown - window):shown]
        out.append(render(f"{tag}{i:02d}", cmd, visible, size))
    return out


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)

    replay = (BUILD / "replay.txt").read_text(encoding="utf-8").splitlines()
    seq = sequence("replay", "python -m contour --replay", replay,
                   frames=16, window=26)
    print(f"  replay   {len(seq)} frames")

    bt = (REPO / "research/strategy_backtest.txt").read_text(
        encoding="utf-8").splitlines()
    seq = sequence("bt", "cat research/strategy_backtest.txt", bt,
                   frames=6, window=22, size=21)
    print(f"  backtest {len(seq)} frames")

    att = subprocess.run(
        [str(REPO / ".venv/bin/python"), "ops/attribution.py", "--offline"],
        cwd=REPO, capture_output=True, text=True).stdout.splitlines()
    seq = sequence("att", "python ops/attribution.py --offline", att,
                   frames=5, window=20, size=21)
    print(f"  attribution {len(seq)} frames")


if __name__ == "__main__":
    main()
