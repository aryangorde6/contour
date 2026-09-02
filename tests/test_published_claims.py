"""The documents make numeric claims about `config.py`. This makes those
claims checkable the same way the journal is.

Twice in one afternoon a published number outlived the code it described: the
write-up promised a 2.8% book ceiling and 2 positions per name long after
config computed 1.678% and 1, and the deck listed the backtest as future work
two days after it ran. Both were found by reading, which does not scale and
does not repeat. A submission whose whole argument is "our claims are
checkable" cannot leave its own claims unchecked.

Scope, stated honestly: this covers numbers that are DERIVED from config, plus
a blocklist of renderings known to be superseded. It cannot police prose, and a
new claim invented in a document is invisible to it until someone adds it here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from contour import config as C

ROOT = Path(__file__).resolve().parent.parent
SURFACES = ("README.md", "WRITEUP.md", "WRITEUP-ONEPAGE.md", "TECHNICAL.md",
            "state.md", "ops/submission.md", "dashboard/deck.html")


def surfaces() -> dict[str, str]:
    out = {}
    for name in SURFACES:
        p = ROOT / name
        if p.exists():
            out[name] = p.read_text(encoding="utf-8")
    assert out, "no published surfaces found -- the paths moved"
    return out


# --- 1. every published percentage is the one config computes -------------
# (label, the rendering the documents use, the value config says it must be)
PUBLISHED_PCT = [
    ("book risk ceiling", "1.678%", f"{C.BOOK_RISK_CEILING_PCT:.3%}"),
    ("sleeve carve-out",  "1.200%", f"{C.SLEEVE_RISK_BUDGET_PCT:.3%}"),
    ("tail carve-out",    "1.122%", f"{C.TAIL_RISK_BUDGET_PCT:.3%}"),
    ("per-position cap",  "1.25%",  f"{C.MAX_POSITION_RISK_PCT:.2%}"),
    ("halt distance",     "4.0%",
     f"{(C.START_NAV - C.NAV_HARD_HALT) / C.START_NAV:.1%}"),
    ("profit target",     "50%",    f"{C.PROFIT_TARGET_PCT_OF_CREDIT:.0%}"),
    ("sleeve stop",       "4%",     f"{C.SLEEVE_STOP_PCT:.0%}"),
]


@pytest.mark.parametrize("label,published,actual",
                         PUBLISHED_PCT, ids=[c[0] for c in PUBLISHED_PCT])
def test_a_published_percentage_still_equals_what_config_computes(
        label, published, actual):
    """If someone retunes a constant, this fails before a judge finds it."""
    assert published == actual, (
        f"the documents publish {published} for the {label}, "
        f"config.py now computes {actual}")


# --- 2. renderings that are known to be superseded ------------------------
# Anchored so 32.8% does not read as 2.8%, and 21.25% does not read as 1.25%.
SUPERSEDED = [
    (r"(?<![\d.])2\.8%",           "the pre-tail 2.8% book ceiling"),
    (r"2 per (name|underlying)",   "the pre-tail 2-positions-per-name cap"),
    (r"third position per name",   "the pre-tail 3-to-2 framing"),
    (r"4\.0% → 2\.8%",             "the two-way carve-out arithmetic"),
]


@pytest.mark.parametrize("pattern,why", SUPERSEDED,
                         ids=[s[1] for s in SUPERSEDED])
def test_no_surface_still_prints_a_superseded_number(pattern, why):
    hits = {name: [m.group(0) for m in re.finditer(pattern, text)]
            for name, text in surfaces().items()
            if re.search(pattern, text)}
    assert not hits, f"{why} still published in: {hits}"


# --- 3. the gate count in prose matches the gates that exist --------------
WORD = {7: "seven", 12: "twelve", 19: "nineteen"}


def test_the_gate_counts_in_prose_match_the_functions_that_exist():
    """`Twelve gates plus seven` is the kind of sentence that survives three
    refactors after it stops being true."""
    opts = len(re.findall(r"^def g\d+_",
                          (ROOT / "contour/gates.py").read_text(), re.M))
    slv = len(re.findall(r"^def s\d+_",
                         (ROOT / "contour/sleeve.py").read_text(), re.M))
    assert (opts, slv) == (12, 7), f"gate functions moved: {opts} + {slv}"

    text = " ".join(surfaces().values()).lower()
    for n in (opts, slv, opts + slv):
        assert WORD[n] in text, (
            f"no surface says '{WORD[n]}' but the code has that many gates")
    # And the counts that would be left behind by a rename must be gone.
    for stale in ("twelve gates plus six", "eleven gates"):
        assert stale not in text, f"stale gate count published: {stale!r}"


# --- 4. the dollar levels and the locked expiry ---------------------------
def test_the_capital_floor_dollars_are_the_ones_the_gate_enforces():
    text = " ".join(surfaces().values())
    for value, label in ((C.NAV_NO_ENTRY, "no-entry floor"),
                         (C.NAV_HARD_HALT, "hard halt")):
        k = f"${int(value / 1000)}k"
        full = f"${int(value):,}"
        assert k in text or full in text, (
            f"no surface publishes the {label} ({k} / {full})")


def test_the_locked_expiry_is_published_as_the_code_locks_it():
    iso = C.EXPIRY.isoformat()
    text = " ".join(surfaces().values())
    assert iso in text, f"the locked expiry {iso} appears on no surface"


# --- 5. the submitted write-up has to stay one page -----------------------
def test_the_one_page_writeup_is_still_one_page():
    """lablab's stated requirement is "One-page write-up covering your AI
    logic, risk gates, and Alpaca infrastructure implementation". WRITEUP.md
    grew from one page to seven without anyone deciding to; the submitted
    copy is the one that has to hold the line, so the line is a test.

    650 words plus a short code block is about a page of dense markdown. This
    is a ceiling, not a target -- shorter is fine."""
    raw = (ROOT / "WRITEUP-ONEPAGE.md").read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", raw, flags=re.S)
    prose = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", prose)   # links -> label only
    words = len(prose.split())
    assert words <= 650, f"the one-pager is {words} words; it is meant to be one page"


def test_the_one_page_writeup_covers_the_three_required_headings():
    """The requirement names three subjects explicitly. Missing one is the
    cheapest possible way to lose points."""
    text = (ROOT / "WRITEUP-ONEPAGE.md").read_text(encoding="utf-8").lower()
    for heading in ("## ai logic", "## risk gates",
                    "## alpaca infrastructure implementation"):
        assert heading in text, f"the one-pager is missing {heading!r}"


def test_the_one_page_writeup_names_the_judged_account():
    """"Alpaca account ID -- required for judging." It appears on the one
    document a judge is guaranteed to open."""
    assert "PA35XVXLIO0E" in (ROOT / "WRITEUP-ONEPAGE.md").read_text(encoding="utf-8")
