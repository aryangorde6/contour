"""The dashboard verifies the hash chain in the judge's own browser.

That only means anything if the JavaScript reaches the same verdict as
`journal.verify()`. The JS does not re-serialise the payload -- it slices the
canonical bytes back out of the line, because `to_line` and `_canonical` use
the same sort_keys/separators, so the nested text is byte-identical to what was
hashed. These tests pin that premise, then run the actual JS from the shipped
page against a chain Python wrote.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from contour import state
from contour.journal import Journal, _canonical

PAGE = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"

# Payloads chosen to break a naive extractor: braces and quotes inside strings,
# an escaped backslash before a quote, non-ASCII, and the float shapes where
# Python and JSON.stringify disagree (0.0 -> "0.0" vs "0").
NASTY = [
    {"event": "cycle_start", "mode": "TRADE", "reason": "entry window open"},
    {"event": "decision", "skew_z": 0.0, "vrp_ratio": 1.385, "nav": 100000.0},
    {"event": "mind", "notes": 'model said {"veto": true} -- a brace "in" a string'},
    {"event": "weird", "s": 'backslash \\" then a close brace }', "n": -0.0},
    {"event": "unicode", "note": "skew ≥ +0.8 → sell puts — café"},
    {"event": "nested", "d": {"a": {"b": [1, 2, {"c": "}"}]}}, "e": None},
]


def _chain(tmp_path: Path) -> Path:
    j = Journal(tmp_path / "2026-08-31.jsonl")
    for p in NASTY:
        j.append(p)
    ok, msg = j.verify()
    assert ok, msg
    return j.path


def _slice_payload(line: str) -> str:
    """The Python twin of the page's payloadText()."""
    k = '"payload":'
    i = line.index(k)
    depth, in_str, esc = 0, False, False
    for n in range(i + len(k), len(line)):
        c = line[n]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return line[i + len(k):n + 1]
    raise AssertionError("unterminated payload")


def test_sliced_payload_is_byte_identical_to_what_was_hashed(tmp_path):
    """The whole browser-side verification rests on this one equality."""
    for line in _chain(tmp_path).read_text().splitlines():
        rec = json.loads(line)
        assert _slice_payload(line) == _canonical(rec["payload"])


# --- the shipped JavaScript, run for real -------------------------------
HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const js = src.slice(src.lastIndexOf("<script>") + 8, src.lastIndexOf("</script>"));
const want = ["sha256", "payloadText", "verifyChain"];
let code = "";
for (const name of want) {
  const m = js.match(new RegExp("^(?:async )?function " + name + "\\b[\\s\\S]*?^}", "m"));
  if (!m) { console.error("could not extract " + name); process.exit(2); }
  code += m[0] + "\n";
}
const GENESIS = "0".repeat(64);
const lines = fs.readFileSync(process.argv[3], "utf8").split("\n").filter(l => l.trim());
(async () => {
  const run = new Function("GENESIS", code + "; return verifyChain;")(GENESIS);
  console.log(JSON.stringify(await run(lines)));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_pages_javascript_agrees_with_python(tmp_path):
    path = _chain(tmp_path)
    harness = tmp_path / "h.js"
    harness.write_text(HARNESS)
    out = subprocess.run(["node", str(harness), str(PAGE), str(path)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    ok, msg, seq = json.loads(out.stdout)
    assert ok, msg
    assert seq == len(NASTY)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_pages_javascript_catches_a_tampered_payload(tmp_path):
    """A dashboard that always says 'verified' is decoration, not evidence."""
    path = _chain(tmp_path)
    lines = path.read_text().splitlines()
    lines[3] = lines[3].replace('"n":-0.0', '"n":-99.0')
    assert '"n":-99.0' in lines[3], "the tamper did not apply"
    path.write_text("\n".join(lines) + "\n")

    harness = tmp_path / "h.js"
    harness.write_text(HARNESS)
    out = subprocess.run(["node", str(harness), str(PAGE), str(path)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    ok, msg, _ = json.loads(out.stdout)
    assert not ok and "tampered at seq 3" in msg

    ok_py, msg_py = Journal(path).verify()
    assert not ok_py and msg_py == msg, "JS and Python must give the same verdict"


# --- the equity series the curve is drawn from --------------------------
def test_equity_series_appends_and_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    for i in range(5):
        state.point("equity", {"nav": 100_000 + i}, cap=3)
    got = json.loads((tmp_path / "equity.json").read_text())
    assert [d["nav"] for d in got] == [100_002, 100_003, 100_004]
    assert all("t" in d for d in got)


def test_equity_series_survives_a_corrupt_file(tmp_path, monkeypatch):
    """Losing the curve is acceptable; failing a trading cycle over it is not."""
    monkeypatch.setattr(state, "ROOT", tmp_path)
    (tmp_path / "equity.json").write_text("{not json at all")
    state.point("equity", {"nav": 100_000.0})
    assert json.loads((tmp_path / "equity.json").read_text())[0]["nav"] == 100_000.0


# --- "structures opened" must mean structures opened --------------------
COUNT_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const js = src.slice(src.lastIndexOf("<script>") + 8, src.lastIndexOf("</script>"));
let code = "";
for (const name of ["countOpened", "countRefused"]) {
  const m = js.match(new RegExp("^function " + name + "\\b[\\s\\S]*?^}", "m"));
  if (!m) { console.error("could not extract " + name); process.exit(2); }
  code += m[0] + "\n";
}
const input = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const f = new Function(code + "; return {countOpened, countRefused};")();
console.log(JSON.stringify({opened: f.countOpened(input.recs),
                            refused: f.countRefused(input.recs, input.dec)}));
"""

# One cycle that looked like two successes and was one: SPY passed the gates
# and then filled; QQQ passed the gates and the LLM vetoed it; IWM passed the
# gates and all three ladder rungs expired unfilled.
CYCLE = {
    "dec": [
        {"underlying": "SPY", "decision": "CONDOR"},
        {"underlying": "QQQ", "decision": "PUT_CS"},
        {"underlying": "IWM", "decision": "CALL_CS"},
        {"underlying": "SPY", "decision": "VETOED"},
    ],
    "recs": [
        {"seq": 1, "payload": {"event": "decision", "underlying": "SPY"}},
        {"seq": 2, "payload": {"event": "position_opened", "underlying": "SPY"}},
        {"seq": 3, "payload": {"event": "mind_confirm", "underlying": "QQQ",
                               "veto": True}},
        {"seq": 4, "payload": {"event": "mind_confirm", "underlying": "SPY",
                               "veto": False}},
        {"seq": 5, "payload": {"event": "no_fill", "base_id": "contour-iwm-x"}},
    ],
}


def _counts(tmp_path, payload):
    harness = tmp_path / "c.js"
    harness.write_text(COUNT_HARNESS)
    data = tmp_path / "in.json"
    data.write_text(json.dumps(payload))
    out = subprocess.run(["node", str(harness), str(PAGE), str(data)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_opened_counts_fills_not_gate_passing_candidates(tmp_path):
    """Two things still stand between a gate-passing decision and the account:
    the LLM veto, which runs after the gates, and the ladder, which can expire
    unfilled at every rung. Counting decisions renders both as wins."""
    got = _counts(tmp_path, CYCLE)
    assert got["opened"] == 1, "counted candidates, not positions"
    assert got["refused"] == {"gated": 1, "llm": 1, "unfilled": 1}


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_a_cycle_with_no_journal_yet_counts_nothing(tmp_path):
    got = _counts(tmp_path, {"dec": None, "recs": None})
    assert got["opened"] == 0
    assert got["refused"] == {"gated": 0, "llm": 0, "unfilled": 0}


# --- the deck is a published page too ------------------------------------
DECK = PAGE.parent / "deck.html"
VIDEO = PAGE.parent.parent / "ops" / "video.md"


def test_the_deck_and_the_dashboard_read_the_same_branch():
    """Both pages fetch the agent's published state. If a rename moves one and
    not the other, the broken one is silently a slide of stale numbers."""
    def consts(p):
        src = p.read_text()
        return {k: re.search(rf'{k}="([^"]+)"', src).group(1)
                for k in ("OWNER", "REPO", "BRANCH")}
    assert consts(DECK) == consts(PAGE)


def test_the_deck_slide_count_matches_what_the_script_narrates():
    """ops/video.md cues slides BY NUMBER, so inserting one silently points
    the narration at the wrong slide. Counting both and comparing is the only
    version of this test that survives the deck growing."""
    slides = DECK.read_text().count('class="slide')
    listed = {int(n) for n in re.findall(r"^\*\*(\d+) — ",
                                        VIDEO.read_text(), re.M)}
    assert slides == 8, f"deck has {slides} slides"
    assert listed == set(range(1, slides + 1)), (
        f"ops/video.md describes slides {sorted(listed)}, the deck has "
        f"{slides}")


def test_the_video_script_timings_are_derived_not_stale():
    """ops/video.md states that its word counts and timings are computed from
    the narration. Hand-estimates were 40% light once already -- a "4:10"
    script that ran 5:03 -- so the claim is enforced rather than repeated."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "video_timing", VIDEO.parent / "video_timing.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    before = VIDEO.read_text(encoding="utf-8")
    after, _total, _secs = mod.rewrite(before)
    assert before == after, "run `python ops/video_timing.py`; timings drifted"


def test_no_published_page_uses_a_root_relative_url():
    """Pages serves these from /contour/, Vercel from the root. A root-relative
    URL works on exactly one of them, and CI greps for this too -- but a red
    test is a faster way to find out than a failed deploy."""
    for p in PAGE.parent.glob("*.html"):
        bad = re.findall(r'(?:src|href)="/[^/][^"]*"', p.read_text())
        assert not bad, f"{p.name}: {bad}"


def test_the_deck_does_not_advertise_a_stale_test_count(request):
    """A number on a slide goes stale silently, and this project's whole claim
    is that its claims are checkable. So the suite counts itself.

    If this fails, the suite grew: put the new number in deck.html. Skipped on
    a subset run, where the count means nothing.
    """
    collected = request.session.testscollected
    if collected < 50:
        pytest.skip(f"subset run ({collected} collected); count is meaningless")
    claimed = {int(n) for n in re.findall(r"(\d+) tests", DECK.read_text())}
    assert claimed == {collected}, (
        f"deck.html says {claimed}, the suite has {collected}")
