"""Render dashboard/deck.html to one PNG per slide.

The slides in the video are the same eight the deck shows, driven through the
deck's own `show(n)` rather than re-drawn here -- so a slide edit reaches the
video the same way it reaches the hosted deck, and the two can never disagree
about a number.

The stage is a fixed 1280x720 that the deck scales to the window, so the
screenshot is taken at twice that and downsampled during assembly, which keeps
the small type crisp.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SP = Path(os.environ.get("CONTOUR_VIDEO_BUILD", REPO / "build/video"))
BUILD = SP / "build"

SLIDES = 8
W, H = 2560, 1440          # 2x the deck's own stage


def shoot(n: int) -> Path:
    """n is 1-based, matching the deck's own numbering."""
    src = (REPO / "dashboard/deck.html").read_text(encoding="utf-8")
    inject = f"""
<script>window.addEventListener('load',function(){{setTimeout(function(){{
  try {{
    show({n - 1});
    ['nav','hint','bar'].forEach(function(id){{
      var e = document.getElementById(id);
      if (e) e.style.display = 'none';
    }});
  }} catch (e) {{}}
}}, 700);}});</script>
"""
    page = BUILD / f"deck-{n}.html"
    page.write_text(src + inject, encoding="utf-8")
    dest = BUILD / f"slide-{n}.png"
    subprocess.run([
        "google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", f"--window-size={W},{H}",
        "--virtual-time-budget=6000",
        f"--screenshot={dest}", f"file://{page}"],
        capture_output=True, check=False)
    return dest


def main() -> int:
    BUILD.mkdir(parents=True, exist_ok=True)
    want = [int(a) for a in sys.argv[1:]] or list(range(1, SLIDES + 1))
    for n in want:
        p = shoot(n)
        ok = p.exists() and p.stat().st_size > 20_000
        print(f"  slide {n}  {'ok' if ok else 'FAILED':6} "
              f"{p.stat().st_size // 1024 if p.exists() else 0} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
