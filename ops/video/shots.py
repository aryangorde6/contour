"""Full-frame screenshots of individual dashboard sections.

Cropping the whole page and upscaling looks soft at 1080p. Instead the page is
copied locally, a script hides every panel except the one wanted, and the shot
is taken at 1920x1080 -- so each frame is native resolution. The copy still
reads its data live from the agent-state branch, so these are the real numbers,
not a fixture.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
# Everything generated lands here; it is gitignored. Override with
# CONTOUR_VIDEO_BUILD to keep renders outside the working tree.
SP = Path(os.environ.get("CONTOUR_VIDEO_BUILD", REPO / "build/video"))
BUILD = SP / "build"
SHOTS = BUILD / "shots"

# Direct children of .wrap, in DOM order. The structure map and the surface
# are NOT in this list: they live inside the .grid2, so `.wrap > .panel` never
# selects them and every index below would be off by one if they were counted.
PANELS = ["sizing", "sleeve", "decisions", "equity", "journal"]

VARIANTS = {
    # name: (JS that hides what we do not want, extra zoom)
    "hero": ("""
        document.querySelectorAll('.wrap .panel, .wrap .grid2')
          .forEach(function(e){ e.style.display='none'; });
        document.querySelector('header').style.paddingBottom='10px';
        document.body.style.zoom = 1.55;
    """),
    "map": ("""
        var g = document.querySelector('.grid2');
        document.querySelectorAll('.wrap > .panel').forEach(function(e){
          e.style.display='none'; });
        if (g) { g.style.gridTemplateColumns='1fr'; }
        var second = g && g.children[1];
        if (second) second.style.display='none';
        document.querySelector('header').style.display='none';
        document.querySelector('.stats').style.display='none';
        document.body.style.zoom = 1.45;
    """),
    "decisions": ("""
        document.querySelectorAll('.wrap .grid2').forEach(function(e){
          e.style.display='none'; });
        var keep = 2;          // "Decisions this cycle"
        document.querySelectorAll('.wrap > .panel').forEach(function(e,i){
          if (i !== keep) e.style.display='none'; });
        document.querySelector('header').style.display='none';
        document.querySelector('.stats').style.display='none';
        document.body.style.zoom = 1.35;
    """),
    "journal": ("""
        document.querySelectorAll('.wrap .grid2').forEach(function(e){
          e.style.display='none'; });
        document.querySelectorAll('.wrap > .panel').forEach(function(e,i){
          if (i !== 4) e.style.display='none'; });   // "Journal"
        document.querySelector('header').style.display='none';
        document.querySelector('.stats').style.display='none';
        document.body.style.zoom = 1.4;
    """),
}


def shoot(name: str, js: str) -> Path:
    src = (REPO / "dashboard/index.html").read_text(encoding="utf-8")
    inject = ("\n<script>window.addEventListener('load',function(){"
              "setTimeout(function(){try{" + js + "}catch(e){}},4200);});"
              "</script>\n")
    page = BUILD / f"page-{name}.html"
    page.write_text(src + inject, encoding="utf-8")
    dest = SHOTS / f"{name}.png"
    subprocess.run([
        "google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--window-size=1920,1080",
        "--virtual-time-budget=11000",
        f"--screenshot={dest}", f"file://{page}"],
        capture_output=True, check=False)
    return dest


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    want = sys.argv[1:] or list(VARIANTS)
    for name in want:
        p = shoot(name, VARIANTS[name])
        print(f"  {name:10} {'ok' if p.exists() else 'FAILED':6} {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
