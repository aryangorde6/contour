"""Assemble the demo video: one segment per cue, narration over visuals.

Each cue gets its audio from narrate.py and its stills from shots.py/frames.py.
Segment length is set by the narration, never the other way round -- so the
picture always changes on the sentence that talks about it, and nothing has to
be re-timed by hand when a line of the script changes.
"""
from __future__ import annotations

import subprocess
import wave
import os
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
# Everything generated lands here; it is gitignored. Override with
# CONTOUR_VIDEO_BUILD to keep renders outside the working tree.
SP = Path(os.environ.get("CONTOUR_VIDEO_BUILD", REPO / "build/video"))
BUILD = SP / "build"
SEG = BUILD / "segments"
FF = imageio_ffmpeg.get_ffmpeg_exe()

W, H = 1920, 1080
BG = (11, 13, 16)
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# cue -> the stills it plays over, in order
PLAN = {
    1: ["slide-1"],
    2: ["shot:map"],
    3: ["shot:hero", "shot:journal"],
    4: ["slide-2"],
    5: [f"frame:replay{i:02d}" for i in range(16)],
    6: ["slide-3"],
    7: [f"frame:bt{i:02d}" for i in range(6)],
    8: ["slide-6"] * 3 + [f"frame:att{i:02d}" for i in range(5)],
    9: ["slide-7"],
}


def resolve(token: str) -> Path:
    if token.startswith("shot:"):
        return BUILD / "shots" / f"{token[5:]}.png"
    if token.startswith("frame:"):
        return BUILD / "frames" / f"{token[6:]}.png"
    return BUILD / f"{token}.png"


def fit(src: Path, dest: Path) -> Path:
    """Letterbox any still onto the 1920x1080 stage on the page's own black,
    trimming the dead space a section screenshot leaves below the content."""
    im = Image.open(src).convert("RGB")
    bbox = im.crop((0, 0, im.width, im.height)).getbbox()
    # getbbox() is relative to black; the page background is near-black, so
    # measure against it instead of trusting a pure-black test.
    grey = im.convert("L").point(lambda v: 255 if v > 26 else 0)
    box = grey.getbbox() or (0, 0, im.width, im.height)
    pad = 26
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(im.width, box[2] + pad), min(im.height, box[3] + pad))
    im = im.crop(box)
    scale = min(W / im.width, H / im.height, 1.6)
    im = im.resize((int(im.width * scale), int(im.height * scale)),
                   Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), BG)
    canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
    canvas.save(dest)
    return dest


def card(dest: Path, lines: list[tuple[str, int, tuple[int, int, int], bool]],
         gap: int = 26) -> Path:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    laid = []
    for text, size, colour, bold in lines:
        f = ImageFont.truetype(MONO_B if bold else MONO, size)
        w = d.textbbox((0, 0), text, font=f)[2]
        laid.append((text, f, colour, w, size))
    total = sum(s for *_, s in laid) + gap * (len(laid) - 1)
    y = (H - total) // 2
    for text, f, colour, w, size in laid:
        d.text(((W - w) // 2, y), text, font=f, fill=colour)
        y += size + gap
    im.save(dest)
    return dest


def duration(wav: Path) -> float:
    with wave.open(str(wav)) as w:
        return w.getnframes() / w.getframerate()


def segment(name: str, stills: list[Path], secs: float,
            audio: Path | None) -> Path:
    per = secs / len(stills)
    listing = SEG / f"{name}.txt"
    body = []
    for p in stills:
        body.append(f"file '{p}'\nduration {per:.4f}")
    body.append(f"file '{stills[-1]}'")          # concat demuxer needs a repeat
    listing.write_text("\n".join(body) + "\n", encoding="utf-8")

    dest = SEG / f"{name}.mp4"
    # Every input must be declared before any output option, and every segment
    # needs an audio track -- concatenating a silent card with a narrated cue
    # fails outright if one of them has no stream to join.
    cmd = [FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", str(listing)]
    cmd += (["-i", str(audio)] if audio
            else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", "30", "-vf", f"scale={W}:{H}",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-shortest", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def main() -> None:
    SEG.mkdir(parents=True, exist_ok=True)
    fitted = BUILD / "fitted"
    fitted.mkdir(exist_ok=True)

    title = card(BUILD / "card-title.png", [
        ("CONTOUR", 118, (240, 244, 250), True),
        ("an options agent that lets the measurement pick the structure",
         34, (150, 158, 172), False),
        ("", 10, BG, False),
        ("FluffyMargins  ·  aryangorde6", 30, (122, 162, 247), False),
        ("Alpaca paper account PA35XVXLIO0E", 28, (124, 132, 148), False),
    ])
    final = card(BUILD / "card-final.png", [
        ("github.com/aryangorde6/contour", 46, (240, 244, 250), True),
        ("aryangorde6.github.io/contour", 40, (122, 162, 247), False),
        ("", 10, BG, False),
        ("PA35XVXLIO0E", 36, (150, 158, 172), False),
        ("paper trading only — not investment advice", 26, (124, 132, 148),
         False),
    ])

    parts = [segment("00-title", [title], 3.4, None)]
    for n in sorted(PLAN):
        wav = BUILD / "audio" / f"cue{n:02d}.wav"
        stills = []
        for i, token in enumerate(PLAN[n]):
            src = resolve(token)
            if not src.exists():
                raise SystemExit(f"missing still {src}")
            stills.append(fit(src, fitted / f"c{n:02d}-{i:02d}.png"))
        secs = duration(wav)
        parts.append(segment(f"{n:02d}-cue", stills, secs, wav))
        print(f"  cue {n}  {secs:5.1f}s  {len(stills)} still(s)")
    parts.append(segment("99-final", [final], 4.2, None))

    listing = SEG / "all.txt"
    listing.write_text("\n".join(f"file '{p}'" for p in parts) + "\n",
                       encoding="utf-8")
    out = SP / "contour-demo.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", str(listing), "-c", "copy", str(out)],
                   check=True, capture_output=True)
    probe = subprocess.run(
        [FF, "-i", str(out)], capture_output=True, text=True).stderr
    line = next((l for l in probe.splitlines() if "Duration" in l), "")
    print(f"\n{out}  {out.stat().st_size // 1024} KB\n{line.strip()}")


if __name__ == "__main__":
    main()
