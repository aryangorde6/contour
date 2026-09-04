"""Build burned-in captions and a sidecar .srt from narrate.py's timings.

The timings come from synthesising one sentence at a time, so a caption is on
screen for exactly as long as the sentence is spoken -- there is no hand-timing
step to drift out of date when a line of the script changes.

Two rules do most of the work in making them comfortable to read. A card that
would flash by too fast is held longer, borrowing from the silence that follows
it; and a gap of a few frames between neighbouring cards is closed, because a
subtitle that blinks off and straight back on is more distracting than one that
simply stays.
"""
from __future__ import annotations

from PIL import ImageFont

MIN_ON = 0.95          # a card shorter than this is uncomfortable to read
CLOSE_GAP = 0.24       # gaps under this flicker, so hold the card instead
TAIL = 0.28            # let the last card outlive the sentence a little

# Captions sit over terminal output and dashboard panels, both of which run to
# the bottom of the frame. An outline alone is legible but visually collides
# with the text underneath, and neither ASS border style draws a usable plate
# in this libass build -- so the plate is drawn explicitly, as a shape behind
# each caption, sized to the lines it actually contains.
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SIZE = 44
MAX_W = 1500           # text column, leaving a wide margin either side
LINE_H = 58
PAD_X, PAD_Y = 32, 18
BOTTOM = 1026          # the plate's lower edge
INK = "&H100D0B&"      # #0B0D10, the page's own near-black, as BBGGRR
PLATE_ALPHA = "&H16&"  # nearly solid: the caption must not compete with
                       # the terminal text it sits over

# ASS colours are &HAABBGGRR -- alpha first, then blue, and 00 is opaque.
STYLE = (
    "Style: Caption,DejaVu Sans,44,"
    "&H00F8F4F2,&H00F8F4F2,&H00101215,&H64000000,"
    "0,0,0,0,100,100,0.3,0,1,2.2,0,2,200,200,54,1"
)

HEAD = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{STYLE}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_font = None


def font() -> ImageFont.FreeTypeFont:
    global _font
    if _font is None:
        _font = ImageFont.truetype(FONT, SIZE)
    return _font


def measure(line: str) -> int:
    return int(font().getlength(line))


def wrap(text: str) -> list[str]:
    """Wrap to the caption column, balancing the two lines rather than filling
    the first -- a long line over a three-word one reads badly."""
    if measure(text) <= MAX_W:
        return [text]
    words = text.split()
    best = None
    for cut in range(1, len(words)):
        a = " ".join(words[:cut])
        b = " ".join(words[cut:])
        wa, wb = measure(a), measure(b)
        if max(wa, wb) > MAX_W:
            continue
        # A dash belongs at the end of the line it interrupts, never at the
        # start of the next one.
        if b[0] in "—–-":
            continue
        if best is None or abs(wa - wb) < best[0]:
            best = (abs(wa - wb), [a, b])
    return best[1] if best else [text]


def settle(rows: list[dict], limit: float | None = None) -> list[dict]:
    """Apply the hold-and-close rules; `limit` is the segment's own length."""
    out = [dict(r) for r in rows]
    for i, row in enumerate(out):
        ceiling = out[i + 1]["start"] if i + 1 < len(out) else (
            row["end"] + TAIL if limit is None else min(row["end"] + TAIL,
                                                        limit))
        if row["end"] - row["start"] < MIN_ON:
            row["end"] = max(row["end"], min(row["start"] + MIN_ON, ceiling))
        if i + 1 < len(out) and out[i + 1]["start"] - row["end"] < CLOSE_GAP:
            row["end"] = out[i + 1]["start"]
        elif i + 1 == len(out):
            row["end"] = ceiling
    return out


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{round(s % 1 * 1000):03d}"


def ass(rows: list[dict], limit: float | None = None) -> str:
    body = []
    for row in settle(rows, limit):
        lines = wrap(row["text"].replace("\n", " ").replace("{", "(")
                     .replace("}", ")"))
        w = max(measure(l) for l in lines) + 2 * PAD_X
        h = len(lines) * LINE_H + 2 * PAD_Y
        x1, x2 = round(960 - w / 2), round(960 + w / 2)
        y1, y2 = BOTTOM - h, BOTTOM
        start, end = _ass_time(row["start"]), _ass_time(row["end"])
        # A short fade reads as a caption appearing rather than a frame change.
        fade = "\\fad(110,110)"
        # Layer 0 is the plate, layer 1 the words, so they cannot fight.
        body.append(
            f"Dialogue: 0,{start},{end},Caption,,0,0,0,,"
            f"{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c{INK}"
            f"\\1a{PLATE_ALPHA}{fade}}}"
            f"m {x1} {y1} l {x2} {y1} {x2} {y2} {x1} {y2}")
        body.append(
            f"Dialogue: 1,{start},{end},Caption,,0,0,0,,"
            f"{{\\an2\\pos(960,{y2 - PAD_Y}){fade}}}"
            + "\\N".join(lines))
    return HEAD + "\n".join(body) + "\n"


def srt(rows: list[dict]) -> str:
    out = []
    for i, row in enumerate(settle(rows), 1):
        out.append(f"{i}\n{_srt_time(row['start'])} --> "
                   f"{_srt_time(row['end'])}\n{row['text']}\n")
    return "\n".join(out)
