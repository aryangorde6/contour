"""The price ladder, and the two ways a fill can be worse than it looks.

A rung that fills PARTIALLY leaves the remainder working at the broker. The
caller writes down a position of `filled_qty` contracts and stops watching the
order, so anything that fills afterwards is risk nobody manages -- the same
class of failure as a position that was never written down at all.

A rung whose legs fill in UNEQUAL quantities is not the structure we designed.
Alpaca fills an mleg atomically, so it should be unreachable; `reconcile` has
computed `legs_balanced` since the first commit and nothing ever read it.
"""
from __future__ import annotations

from contour import config as C
from contour.execute import BrokerError, submit_with_ladder

from .test_manage import condor


def order(status: str, qty: int, filled: int, leg_qtys=(1, 1, 1, 1)) -> dict:
    return {
        "id": "ord1", "status": status, "qty": qty, "filled_qty": filled,
        "legs": [{"symbol": f"L{i}", "side": "sell" if i % 2 == 0 else "buy",
                  "filled_qty": q, "filled_avg_price": 1.0}
                 for i, q in enumerate(leg_qtys)],
    }


class Broker:
    """Serves a scripted sequence of order reads. The last one repeats."""

    def __init__(self, reads):
        self.reads = list(reads)
        self.canceled: list[str] = []
        self.cancel_raises = False

    def submit_mleg(self, legs, qty, price, coid, dry_run=False):
        return {"id": "ord1", "status": "accepted"}

    def get_order(self, oid):
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]

    def cancel(self, oid):
        self.canceled.append(oid)
        if self.cancel_raises:
            raise BrokerError("cancel rc=1: order already in a terminal state")


def run(broker, events):
    return submit_with_ladder(broker, condor(), "base", events.append,
                              rung_seconds=0, sleep=lambda _: None)


# --- the residual ---------------------------------------------------------
def test_a_partial_fill_cancels_the_rest_before_we_look_away():
    """Two of three contracts filled and the order is still working. Whatever
    fills after this point is untracked: the book is about to be written."""
    events = []
    b = Broker([order("new", qty=3, filled=2),
                order("canceled", qty=3, filled=2)])
    rec = run(b, events)

    assert b.canceled == ["ord1"], "the working remainder was left at the broker"
    assert rec["filled_qty"] == 2
    kinds = [e["event"] for e in events]
    assert "residual_canceled" in kinds
    assert kinds.index("residual_canceled") < kinds.index("filled"), (
        "cancel first, then record -- the other order records a position "
        "while the rest of it can still fill")


def test_the_record_is_the_read_taken_after_the_cancel():
    """The cancel races the book. A contract that filled inside that window is
    ours whether we wanted it or not, and the position must say so."""
    events = []
    b = Broker([order("new", qty=3, filled=1),
                order("canceled", qty=3, filled=2)])
    rec = run(b, events)
    assert rec["filled_qty"] == 2, "recorded the pre-cancel read"


def test_a_terminal_fill_is_not_cancelled():
    events = []
    b = Broker([order("filled", qty=1, filled=1)])
    rec = run(b, events)
    assert b.canceled == [], "cancelling a filled order is a 422, not a no-op"
    assert rec["filled_qty"] == 1
    assert "residual_canceled" not in [e["event"] for e in events]


def test_a_failed_cancel_is_journaled_and_does_not_lose_the_fill():
    """The order going terminal a millisecond before our cancel is the normal
    race, not an emergency. Record it and carry on with what filled."""
    events = []
    b = Broker([order("new", qty=3, filled=2),
                order("filled", qty=3, filled=3)])
    b.cancel_raises = True
    rec = run(b, events)
    assert rec["filled_qty"] == 3
    assert "residual_cancel_failed" in [e["event"] for e in events]


# --- the structure that is not the one we designed ------------------------
def test_unequal_leg_fills_raise_the_alarm():
    """A short without its wing is naked. This must never be silent."""
    events = []
    b = Broker([order("filled", qty=2, filled=2, leg_qtys=(2, 1, 2, 2))])
    rec = run(b, events)

    assert rec["legs_balanced"] is False
    alarm = [e for e in events if e["event"] == "unbalanced_fill"]
    assert alarm, "legs_balanced was computed and thrown away again"
    assert "repair" in alarm[0]["reason"]


def test_a_balanced_fill_raises_nothing():
    events = []
    b = Broker([order("filled", qty=2, filled=2, leg_qtys=(2, 2, 2, 2))])
    rec = run(b, events)
    assert rec["legs_balanced"] is True
    assert "unbalanced_fill" not in [e["event"] for e in events]


# --- the ladder itself, unchanged ----------------------------------------
def test_every_rung_expires_unfilled_and_the_ladder_gives_up():
    events = []
    b = Broker([order("canceled", qty=2, filled=0)])
    rec = run(b, events)
    assert rec["status"] == "NO_FILL" and rec["filled_qty"] == 0
    assert [e["event"] for e in events].count("rung_expired") == len(C.LADDER_RUNGS)
    assert events[-1]["event"] == "no_fill"


def test_each_rung_submits_a_distinct_client_order_id():
    """Alpaca 422s on a reused client_order_id, so a ladder that reuses one is
    rejected on rung two by construction."""
    seen = []

    class Watcher(Broker):
        def submit_mleg(self, legs, qty, price, coid, dry_run=False):
            if not dry_run:
                seen.append(coid)
            return {"id": "ord1", "status": "accepted"}

    b = Watcher([order("canceled", qty=2, filled=0)])
    run(b, [])
    assert seen == [f"base-r{i}" for i in range(1, len(C.LADDER_RUNGS) + 1)]
    assert len(set(seen)) == len(seen)
