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
uv pip install piper-tts imageio-ffmpeg          # not runtime deps
python -m piper.download_voices en_US-ryan-high  # ~120 MB, into build/video/voices

python ops/video/narrate.py    # ops/video.md  -> one WAV per cue
python ops/video/shots.py      # dashboard sections at 1920x1080
python ops/video/frames.py     # terminal output as frame sequences
python ops/video/assemble.py   # -> build/video/contour-demo.mp4
```

Segment length comes from the narration, never the other way round, so the
picture always cuts on the sentence that talks about it.

**Swapping the voice** is one constant: `VOICE` in `narrate.py`. Any piper
voice works; `en_US-lessac-high` and `en_GB-northern_english_male-medium` are
reasonable alternatives.

Output lands in `build/video/`, which is gitignored — the renders are large
and reproducible, so they are not committed. The pipeline is.
