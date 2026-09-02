"""Generate the lablab submission cover image.

A submission field, and the one asset a judge sees before they see anything
else. Kept as a script rather than a screenshot so it regenerates when a
number changes -- the account ID and the structure map both appear on it, and
both have moved once already.

    .venv/bin/python ops/make_cover.py     ->  dashboard/cover.png  (1280x720)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG      = "#0a0c0f"
TXT     = "#e8eef4"
DIM     = "#8b98a6"
FAINT   = "#5c6874"
LINE    = "#1e252d"
AMBER   = "#f0b429"
GREEN   = "#46d18a"
SUNK    = "#0d1115"

FONTS = {
    "sans":      ["Ubuntu-R.ttf", "DejaVuSans.ttf"],
    "sans_bold": ["Ubuntu-B.ttf", "DejaVuSans-Bold.ttf"],
    "sans_med":  ["Ubuntu-M.ttf", "DejaVuSans-Bold.ttf"],
    "mono":      ["UbuntuMono-R.ttf", "DejaVuSansMono.ttf"],
    "mono_bold": ["UbuntuMono-B.ttf", "DejaVuSansMono-Bold.ttf"],
}
ROOTS = ["/usr/share/fonts/truetype/ubuntu", "/usr/share/fonts/truetype/dejavu"]


def has_glyph(f: ImageFont.FreeTypeFont, ch: str) -> bool:
    """PIL renders a missing glyph as .notdef -- a hollow box -- rather than
    raising, so an uncovered character reaches the PNG as tofu and nothing
    complains. The arrow did exactly that on the first render: Ubuntu Mono has
    no U+2192. Compare against a codepoint no font covers."""
    return bytes(f.getmask(ch, mode="L")) != bytes(f.getmask("\ue000", mode="L"))


def font(kind: str, size: int, need: str = "") -> ImageFont.FreeTypeFont:
    """Falls through the candidate list until one actually covers `need`,
    instead of taking the first that merely exists."""
    for name in FONTS[kind]:
        for root in ROOTS:
            p = Path(root) / name
            if p.exists():
                f = ImageFont.truetype(str(p), size)
                if all(has_glyph(f, c) for c in need):
                    return f
    raise SystemExit(f"no {kind} font on this machine covers {need!r}")


def draw(d: ImageDraw.ImageDraw, xy, text, f, fill):
    """Single choke point, so nothing reaches the PNG as a hollow box."""
    missing = {c for c in text if not c.isspace() and not has_glyph(f, c)}
    if missing:
        raise SystemExit(f"missing glyphs {sorted(missing)!r} in {text!r}")
    d.text(xy, text, font=f, fill=fill)


def tracked(d: ImageDraw.ImageDraw, xy, text, f, fill, extra=0.0):
    """Letter-spaced text. PIL has no tracking, and the eyebrow label needs it
    to read as a label rather than as small body copy."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + extra
    return x


def main() -> Path:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    M = 76

    # eyebrow
    tracked(d, (M, 62), "ALPACA AI TRADING AGENTS HACKATHON",
            font("sans_med", 15), FAINT, 2.6)
    tracked(d, (M, 86), "OPTIONS ALPHA AGENTS",
            font("sans_med", 15), GREEN, 2.6)

    # wordmark
    draw(d, (M - 4, 126), "Contour", font("sans_bold", 104), TXT)
    draw(d, (M, 252), "the measurement picks the structure", font("sans", 33), DIM)

    d.line([(M, 320), (W - M, 320)], fill=LINE, width=1)

    # the four-line rule -- the single most distinctive thing about the agent
    rows = [("vrp_ratio < 1.30", "NO_TRADE",           "implied is not rich enough"),
            ("skew_z ≥ +0.8",  "PUT_CREDIT_SPREAD",  "puts rich — sell puts"),
            ("skew_z ≤ −0.8", "CALL_CREDIT_SPREAD", "calls rich — sell calls"),
            ("otherwise",        "IRON_CONDOR",        "both fair — sell both")]
    fc, fs, fw = font("mono", 26), font("mono_bold", 26), font("sans", 19)
    fa = font("mono", 24, need="→")
    y = 356
    for cond, struct, why in rows:
        draw(d, (M, y), cond, fc, AMBER)
        draw(d, (M + 258, y + 2), "→", fa, FAINT)
        draw(d, (M + 300, y), struct, fs, TXT)
        draw(d, (M + 300 + d.textlength(struct, font=fs) + 22, y + 5),
             why, fw, FAINT)
        y += 50

    # footer
    d.line([(M, 596), (W - M, 596)], fill=LINE, width=1)
    draw(d, (M, 624), "SPY  ·  QQQ  ·  IWM  ·  one locked expiry", font("sans", 21), DIM)

    pill, fp = "paper account PA35XVXLIO0E", font("mono", 19)
    pw = d.textlength(pill, font=fp)
    x0, y0, x1, y1 = W - M - pw - 30, 618, W - M, 656
    d.rounded_rectangle([x0, y0, x1, y1], radius=19, fill=SUNK, outline=LINE)
    draw(d, (x0 + 15, y0 + 10), pill, fp, DIM)

    d.rectangle([0, H - 3, W, H], fill=GREEN)

    out = Path(__file__).resolve().parent.parent / "dashboard" / "cover.png"
    img.save(out, "PNG")
    return out


if __name__ == "__main__":
    p = main()
    print(f"{p}  {Image.open(p).size}  {p.stat().st_size / 1024:.0f} KB")
