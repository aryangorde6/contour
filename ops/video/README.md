# The demo video, generated from the script

`ops/video.md` is the narration. This pipeline turns it into
`contour-demo.mp4` without anyone reading it aloud, so a wording change is a
re-run rather than a re-shoot — the same argument the rest of this repo makes
about its numbers, applied to its own pitch.

Nothing here invents footage. The dashboard frames are screenshots of the
live page reading the `agent-state` branch, the terminal frames are real
captured output from `--replay`, `research/strategy_backtest.txt` and
`ops/attribution.py --offline`, and the slides are `dashboard/deck.html`.

```bash
uv pip install kokoro-onnx imageio-ffmpeg          # not runtime deps

# the voice, ~350 MB, into ~/.local/share/contour-voices
curl -sSLO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -sSLO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

python ops/video/narrate.py    # ops/video.md -> a WAV per cue + timing.json
python ops/video/slides.py     # dashboard/deck.html -> one PNG per slide
python ops/video/shots.py      # dashboard sections at 1920x1080
python ops/video/frames.py     # terminal output as frame sequences
python ops/video/assemble.py   # -> build/video/contour-demo.mp4 + .srt
```

Segment length comes from the narration, never the other way round, so the
picture always cuts on the sentence that talks about it. The slides are driven
through the deck's own `show(n)`, so a slide edit reaches the video the same
way it reaches the hosted deck and the two cannot disagree about a number --
run `python ops/deck_fit.py --check` after editing one.

## Sounding like a person

Sentences are synthesised one at a time and reassembled with pauses set here,
not left to the engine's idea of a full stop — a reader breathes longer
between paragraphs than between sentences. Each sentence is then trimmed of
the engine's own padding so those pauses are the only thing setting the pace,
and the assembled cue is compressed and normalised to −16 LUFS, which is the
polish a recorded voice-over would get.

The text is also rewritten to what a presenter would say out loud —
"execute dot p y", not "execute dot pie". Only the spoken form is respelled;
the caption keeps the written one.

**Swapping the voice** is one environment variable: `CONTOUR_VOICE`, any of
Kokoro's 54 (`am_michael` is the default; `am_fenrir`, `bm_george` and
`af_heart` are the other good narrators). Without the Kokoro model present,
`narrate.py` falls back to Piper and says so.

## Captions

Synthesising per sentence means we know exactly when each one starts, so the
captions fall out of the same pass — there is no hand-timing step to drift out
of date when a line of the script changes. `narrate.py` writes `timing.json`;
`subtitles.py` turns it into burned-in ASS per cue and one sidecar
`contour-demo.srt` for the video host.

Captions get a reserved band at the foot of the frame and the picture is
composed above it, rather than the words being laid over the content. Burned-in
subtitles that cover the last few lines of a terminal are worse than none, and
a plate opaque enough to be readable hides those lines just the same.

Output lands in `build/video/`, which is gitignored — the renders are large
and reproducible, so they are not committed. The pipeline is.
