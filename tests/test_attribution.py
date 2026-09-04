"""The provenance claim, pinned.

The submission tells a judge that the agent's P&L and the operator's P&L are
separable, and that the separation rests on a field the BROKER records rather
than on our own bookkeeping. That claim has exactly one load-bearing
assumption: every order this codebase submits carries a client_order_id
starting `contour-`, and nothing else in the account does.

Delete the prefix from one id constructor and the attribution silently
reclassifies the agent's own trades as somebody else's -- in the flattering
direction. So it is tested here rather than observed once and trusted.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contour import config as C
from contour.loop import close_base_id, order_base_id, sleeve_base_id

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "attribution", ROOT / "ops/attribution.py")
attribution = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attribution)

NOW = datetime(2026, 9, 2, 15, 7, tzinfo=timezone.utc)


# --- 1. the prefix invariant ---------------------------------------------
@pytest.mark.parametrize("underlying", ["SPY", "QQQ", "IWM"])
@pytest.mark.parametrize("structure", ["condor", "put_spread", "call_spread"])
def test_every_options_order_id_is_attributable_to_the_agent(underlying,
                                                             structure):
    assert order_base_id(underlying, structure, NOW).startswith(
        attribution.AGENT_PREFIX)


def test_the_sleeve_order_id_is_attributable_to_the_agent():
    assert sleeve_base_id(NOW).startswith(attribution.AGENT_PREFIX)


class _Pos:
    """close_base_id only reads .order_id."""
    order_id = "1b003d45-9655-4dd8-bb69-adcee798ff44"


def test_close_ids_carry_the_prefix_too():
    """The original version of this test asserted that close_base_id mentions
    `pos.order_id`, and passed while the bug was live -- because pos.order_id
    is the BROKER's order id, not the client id the agent chose, so the exit
    carried no prefix at all. The flatten then closed the SPY condor and the
    whole symbol moved into the operator's column, overstating the agent.

    Assert the property that actually matters instead of a proxy for it."""
    assert close_base_id(_Pos(), NOW).startswith(attribution.AGENT_PREFIX)


def test_an_exit_named_after_an_agent_entry_is_still_the_agents():
    """Exits placed before the prefix existed are on the record and cannot be
    renamed, so attribution resolves them through the entry they point at."""
    entry = "b7c1e0aa-0000-4000-8000-000000000001"
    snap = {"orders": [
        {"client_order_id": "contour-spy-abc123-r0", "order_id": entry},
        {"client_order_id": f"{entry}-x20260903T1503-0", "order_id": "other"},
    ]}
    roots = attribution.agent_ids(snap)
    assert attribution.placed_by_agent(f"{entry}-x20260903T1503-0", roots)
    assert not attribution.placed_by_agent("tail-178827825", roots)


# --- 2. the export a reader without credentials reproduces ----------------
def test_the_committed_export_exists_and_parses():
    """`--offline` is the whole point: the number has to be checkable by
    someone who does not have our keys."""
    snap = json.loads(attribution.EXPORT.read_text(encoding="utf-8"))
    assert snap["account"] == "PA35XVXLIO0E"
    assert snap["start_equity"] == C.START_NAV
    assert snap["orders"], "an export with no orders proves nothing"


def test_the_export_reconciles_to_broker_equity():
    """Fills plus marks must add up to what the broker says the account is
    worth. If they do not, the split is arithmetic we invented."""
    snap = json.loads(attribution.EXPORT.read_text(encoding="utf-8"))
    att = attribution.attribute(snap)
    drift = abs(att["total_pnl"] - att["broker_pnl"])
    assert drift <= attribution.RECONCILE_TOLERANCE, (
        f"{drift:,.2f} of P&L is unexplained by any fill")


def test_no_order_in_the_export_is_left_unclassified():
    snap = json.loads(attribution.EXPORT.read_text(encoding="utf-8"))
    for o in snap["orders"]:
        assert o["client_order_id"], f"{o['symbol']} order carries no id"


def test_the_export_carries_no_credentials():
    """It is committed, so it gets read by strangers."""
    raw = attribution.EXPORT.read_text(encoding="utf-8")
    assert not re.search(r"(?i)(secret|api[_-]?key|PK[A-Z0-9]{14})", raw)


# --- 3. the split the documents publish -----------------------------------
def _published_snapshot() -> tuple[str, float, float]:
    """Recompute the split from the committed export, the way the docs quote
    it: a UTC minute and two percentages."""
    snap = json.loads(attribution.EXPORT.read_text(encoding="utf-8"))
    att = attribution.attribute(snap)
    return (snap["captured_at"][:16],
            att["agent_pnl"] / snap["start_equity"],
            att["human_pnl"] / snap["start_equity"])


SURFACES = ("WRITEUP.md", "WRITEUP-ONEPAGE.md", "dashboard/deck.html",
            "ops/video.md")


@pytest.mark.parametrize("rel", SURFACES)
def test_a_published_attribution_matches_the_export(rel):
    """Any document quoting the split must quote THIS export's split. The
    marks move every session, so the documents carry the export's timestamp
    and the test makes the two move together -- a stale figure fails rather
    than quietly ageing."""
    # Markdown uses a typographic minus and the deck an HTML entity; Python's
    # % formatting emits a hyphen. Normalise rather than pushing ASCII
    # punctuation into the documents to satisfy a test.
    text = (ROOT / rel).read_text(encoding="utf-8")
    text = text.replace("−", "-").replace("&minus;", "-")
    stamp, agent, human = _published_snapshot()
    if "ATTRIBUTION-SNAPSHOT" not in text:
        pytest.skip(f"{rel} publishes no attribution figure")
    assert stamp in text, (
        f"{rel} quotes an attribution from a different capture than "
        f"ops/order_history.json ({stamp}); rerun ops/attribution.py and "
        f"update it")
    for label, value in (("agent", agent), ("operator", human)):
        assert f"{value:+.2%}" in text or f"{value:.2%}" in text, (
            f"{rel} does not carry the {label} figure {value:.2%} from the "
            f"export")


# --- 4. the refresh path itself -------------------------------------------
def test_publishing_the_committed_export_changes_nothing():
    """`--publish` rewrites three tables in two markup languages, and it gets
    run once more after the final flatten -- late, and under time pressure. If
    the rewriter does not reproduce the committed documents byte for byte on
    input it has already published, it is reformatting as well as updating, and
    that would land unreviewed."""
    snap = json.loads(attribution.EXPORT.read_text(encoding="utf-8"))
    before = {rel: (ROOT / rel).read_text(encoding="utf-8")
              for rel in attribution.PUBLISHED}
    try:
        changed = attribution.publish(snap, attribution.attribute(snap))
    finally:                       # it writes in place; never leave a mess
        for rel, text in before.items():
            (ROOT / rel).write_text(text, encoding="utf-8")
    assert changed == [], f"publish() would reformat {changed} with no new data"
