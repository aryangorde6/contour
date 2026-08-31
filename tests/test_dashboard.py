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
