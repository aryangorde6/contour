"""The LLM layer. Its only wired outputs NARROW what the deterministic engine
may do -- it can never widen anything.

This is enforced structurally, not by convention: execute.py never imports this
module, so no LLM output can reach an order. What the model returns is a set of
time windows to stand down in, a size multiplier bounded at 1.0, and a boolean
veto. It cannot select a strike, size a position, price an order, or reverse a
structure. Those are arithmetic, and language models should not do arithmetic
that money depends on.

Failure policy, deliberately two-tiered:
  - NOT CONFIGURED (no API key): run degraded at multiplier 0.5 on the
    hard-coded G10 fallback blackout table. A system that stops trading because
    a language model is absent is not autonomous, it is dependent.
  - CONFIGURED BUT FAILING (timeout, parse error, off-schema): fail CLOSED --
    veto the trade. A configured brain returning garbage is a real signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal, Sequence

from pydantic import BaseModel, Field

from . import config as C
from .llm import AnthropicProvider, Provider, build_provider
from .models import Blackout

# The macro regime is hard-coded into the prompt on purpose. A model left to
# its training prior will assume weak data is bearish for equities, which is
# exactly backwards for this week, and would invert the regime call on the one
# morning it matters most.
REGIME_BRIEF = """\
Market context for the week of 2026-08-31, treat as ground truth:
- Fed Chair is Kevin Warsh. Fed funds 3.50-3.75%.
- After Warsh's Jackson Hole speech on 2026-08-28, CME-implied odds of a 25bp
  HIKE at the Sep 15-16 FOMC are about 60%. The market is pricing a HIKE, not
  a cut.
- Consequently WEAK economic data is currently BULLISH for equities: on
  2026-08-07 a -23K payroll print produced a record S&P close. Do not apply
  the ordinary "bad news is bad" prior; it is inverted right now.
- VIX closed 14.35, a 2026 low. SPY 769.35, near record highs. Realized vol on
  SPY is roughly 7.6% annualized, about half of implied.
- This week: ISM Mfg + JOLTS Tue 10:00 ET, ADP Wed 08:15 ET, Beige Book Wed
  14:00 ET, ISM Services Thu 10:00 ET, Fed speakers Hammack and Goolsbee Thu,
  August NFP Fri 08:30 ET. FOMC blackout does not begin until Sat Sep 5, so
  Fed speakers are live and unfiltered all week.
"""

AGENT_BRIEF = """\
You advise a defined-risk options agent trading SPY, QQQ and IWM credit
spreads and iron condors on the 2026-09-11 expiry, in an Alpaca paper account.
You do not choose strikes, sizes or prices -- deterministic code owns all of
those. Your outputs can only make the agent trade LESS.
"""


class BlackoutWindow(BaseModel):
    start_et: str = Field(description="24h ET start time, HH:MM")
    end_et: str = Field(description="24h ET end time, HH:MM")
    reason: str = Field(description="the scheduled event, named")


class BlackoutPlan(BaseModel):
    windows: list[BlackoutWindow]
    notes: str


class Regime(BaseModel):
    size: Literal["FULL", "HALF", "NONE"]
    no_new_entries_after: Literal["11:00", "13:00", "15:15"]
    rationale: str


class Verdict(BaseModel):
    veto: bool
    reason: str


MULTIPLIER = {"FULL": 1.0, "HALF": 0.5, "NONE": 0.0}


@dataclass
class Advice:
    blackouts: tuple[Blackout, ...]
    multiplier: float
    no_new_entries_after: datetime | None
    source: str          # "llm" | "degraded" | "failed_closed"
    notes: str


def _et(day: date, hhmm: str) -> datetime:
    """Raises on anything that is not HH:MM. Callers drop the single window
    rather than the whole answer: one mis-formatted time in one blackout is
    not a reason to fail the cycle closed and veto every entry."""
    h, m = (int(x) for x in str(hhmm).strip().split(":")[:2])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"time out of range: {hhmm!r}")
    return datetime.combine(day, time(h, m), tzinfo=C.ET)


class Mind:
    """Wraps Claude. Every method is total -- it returns a safe value rather
    than raising, because a cycle must never die on the advisory layer."""

    def __init__(self, api_key: str | None = None,
                 provider: Provider | None = None):
        if provider is not None:
            self.provider: Provider | None = provider
        elif api_key is not None:
            # An explicit key names an explicit vendor. Empty means absent,
            # which is tier one of the failure policy, not an error.
            self.provider = AnthropicProvider(api_key) if api_key else None
        else:
            self.provider = build_provider()

    @property
    def configured(self) -> bool:
        return self.provider is not None

    @property
    def brain(self) -> str:
        """What is actually answering, for the journal. The write-up claims
        reproducibility; that claim needs the model named in the record."""
        p = self.provider
        return f"{p.name}:{p.model}" if p is not None else "degraded"

    def _call(self, system: str, user: str, schema, effort: str = "low"):
        """The one seam. Every caller treats a raise as 'the brain failed'."""
        assert self.provider is not None
        return self.provider.parse(system, user, schema, effort=effort)

    # --- 1. event windows: the genuinely LLM-shaped job -------------------
    def blackouts(self, day: date, headlines: Sequence[str] = ()) -> Advice:
        if not self.configured:
            return Advice((), 0.5, None, "degraded",
                          "no LLM provider configured; running on the hard-coded "
                          "G10 fallback table at half size")
        try:
            plan = self._call(
                AGENT_BRIEF + REGIME_BRIEF,
                    f"Today is {day:%A %Y-%m-%d}. Return the intervals during "
                    f"today's 09:30-16:00 ET session when a short-premium "
                    f"options agent should NOT open new positions, because a "
                    f"scheduled macro release or Fed speaker falls inside or "
                    f"immediately before them. Bracket each event generously "
                    f"-- roughly 20 minutes either side. If nothing is "
                    f"scheduled today, return an empty list rather than "
                    f"inventing caution.\n\nOvernight headlines:\n"
                    + ("\n".join(f"- {h}" for h in headlines) or "- (none)"),
                BlackoutPlan, effort="medium")
            # Drop a window we cannot parse; do not drop the answer. Failing
            # the whole call closed on one bad time string would veto every
            # entry for the cycle -- a far larger action than the defect.
            windows, dropped = [], []
            for w in plan.windows:
                try:
                    windows.append(Blackout(_et(day, w.start_et),
                                            _et(day, w.end_et), w.reason))
                except (ValueError, TypeError, AttributeError):
                    dropped.append(f"{w.start_et!r}-{w.end_et!r}")
            notes = plan.notes
            if dropped:
                notes = f"{notes} | UNPARSEABLE windows dropped: {', '.join(dropped)}"
            return Advice(tuple(windows), 1.0, None, "llm", notes)
        except Exception as exc:                                # noqa: BLE001
            return Advice((), 0.0, None, "failed_closed",
                          f"blackout parse failed: {type(exc).__name__}: {exc}")

    # --- 2. regime multiplier: three integers from a fixed menu -----------
    def regime(self, day: date, vrp: dict[str, float]) -> Advice:
        if not self.configured:
            return Advice((), 0.5, None, "degraded",
                          "no LLM provider configured; half size")
        try:
            g = self._call(
                AGENT_BRIEF + REGIME_BRIEF,
                    f"Today is {day:%A %Y-%m-%d}. Measured implied/realized "
                    f"volatility ratios right now: "
                    + ", ".join(f"{k} {v:.2f}" for k, v in vrp.items())
                    + ".\n\nChoose a size posture and a cutoff after which no "
                      "new positions may be opened. FULL only when the session "
                      "carries no scheduled macro risk and the vol premium is "
                      "genuinely being paid. NONE when you would not want a "
                      "short-premium book open at all today.",
                Regime, effort="low")
            return Advice((), MULTIPLIER[g.size],
                          _et(day, g.no_new_entries_after), "llm", g.rationale)
        except Exception as exc:                                # noqa: BLE001
            return Advice((), 0.0, None, "failed_closed",
                          f"regime call failed: {type(exc).__name__}: {exc}")

    # --- 3. structure veto: it may refuse, never approve into existence ---
    def confirm(self, underlying: str, structure: str, vrp_ratio: float,
                skew_z: float, headlines: Sequence[str] = ()) -> Verdict:
        if not self.configured:
            return Verdict(veto=False,
                           reason="LLM not configured; deterministic gates only")
        try:
            return self._call(
                AGENT_BRIEF + REGIME_BRIEF,
                    f"The engine intends to open a {structure} on {underlying}. "
                    f"Measured: implied/realized {vrp_ratio:.2f}, 25-delta skew "
                    f"z-score {skew_z:+.2f}.\n\nHeadlines:\n"
                    + ("\n".join(f"- {h}" for h in headlines) or "- (none)")
                    + "\n\nVeto ONLY if something in the headlines means this "
                      "premium is rich for a reason the measurement cannot "
                      "see -- an unpriced binary event, a pending "
                      "announcement, a halt. Richness alone is not a veto; "
                      "that is the entire strategy. You cannot modify the "
                      "trade, only refuse it.",
                Verdict, effort="low")
        except Exception as exc:                                # noqa: BLE001
            return Verdict(veto=True,
                           reason=f"fail-closed: {type(exc).__name__}: {exc}")
