"""Check that no deck slide overflows its stage.

The deck is a fixed 1280x720 stage that every slide is absolutely positioned
inside, so a slide with one paragraph too many does not scroll -- it runs its
last bullet through the footer on screen, and gets silently clipped in the
printed PDF. Both surfaces are submitted, and neither complains.

This renders the deck headless and measures, for every slide, the distance
between the lowest piece of content and the top of the footer. Run it after
editing a slide.

    python ops/deck_fit.py            # report every slide
    python ops/deck_fit.py --check    # exit 1 if any slide is too tight
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DECK = REPO / "dashboard/deck.html"
MIN_GAP = 6          # px between the last line of content and the footer

# Measure each slide in turn: make it visible, find the lowest content box,
# and compare it against the footer. Text nodes are measured through their
# elements, so `li` and `p` are what matter -- `ul` would fold in its own
# trailing margin and report an overflow that is not visible.
PROBE = """
<script>window.addEventListener('load',function(){setTimeout(function(){
  var out=[];
  document.querySelectorAll('.slide').forEach(function(s,i){
    var prev=s.className;
    s.classList.add('on');
    var foot=s.querySelector('.foot');
    var footTop=foot?foot.getBoundingClientRect().top:null;
    var lowest=-1, who='';
    s.querySelectorAll('li,p,h1,h2,h3,table,.card').forEach(function(e){
      if (foot && foot.contains(e)) return;
      var b=e.getBoundingClientRect().bottom;
      if (b>lowest){ lowest=b; who=(e.textContent||'').trim().slice(0,52); }
    });
    out.push({slide:i+1, gap: footTop===null?null:Math.round(footTop-lowest),
              who:who.replace(/\\s+/g,' ')});
    s.className=prev;
  });
  document.title='DECKFIT'+JSON.stringify(out);
},900);});</script>
"""


def measure() -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "deck-probe.html"
        page.write_text(DECK.read_text(encoding="utf-8") + PROBE,
                        encoding="utf-8")
        out = subprocess.run(
            ["google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
             "--window-size=1280,720", "--virtual-time-budget=5000",
             "--dump-dom", f"file://{page}"],
            capture_output=True, text=True).stdout
    m = re.search(r"DECKFIT(\[.*?\])</title>", out)
    if not m:
        raise SystemExit("could not measure the deck -- is google-chrome "
                         "installed?")
    return json.loads(m.group(1))


def main() -> int:
    rows = measure()
    tight = [r for r in rows if r["gap"] is not None and r["gap"] < MIN_GAP]
    for r in rows:
        gap = "n/a" if r["gap"] is None else f"{r['gap']:>5}"
        flag = "  <-- overflows" if r in tight else ""
        print(f"  slide {r['slide']}  gap {gap} px   {r['who'][:46]}{flag}")
    if "--check" in sys.argv and tight:
        print(f"\n{len(tight)} slide(s) under {MIN_GAP}px of clearance; the "
              f"printed PDF clips what the screen overlaps.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
