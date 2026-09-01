"""Strike selection from the traded distribution, not the modelled one.

`structures.assemble` picks short strikes by delta. Delta is the risk-neutral
probability of finishing in the money under a lognormal centred on spot -- a
smooth, symmetric, memoryless distribution. The tape is none of those things.
A volume profile measures where price ACTUALLY traded: the POC is the modal
price, and the value area is the band holding VALUE_AREA_PCT of the volume.

When the two disagree, the model is selling a strike the tape says is busy.

**The measurement.** Five years of daily bars on SPY, QQQ and IWM (2021-09-03
to 2026-09-01, 1253 bars each). Distance is held FIXED in sigma units, so the
strike is identical either way and only the value area's position moves; the
only thing being tested is whether the profile carries information delta does
not. Horizon 8 trading days, matching the expiry the agent actually trades.
At 1.13 sigma -- the 0.13-delta short strike this module feeds -- an eight-day
touch happens:

    call strike INSIDE the value area   32.8%  (247/753)
    call strike OUTSIDE the value area  21.9%  (627/2859)   z = +6.20

That is the entire claim: 10.9 points of touch probability, at a strike the
delta band already called acceptable. It holds in every vol regime (+6.8,
+14.3, +20.4 points for high/mid/low), in all three names (SPY +15.9, QQQ
+12.9, IWM +4.8) and in five of six calendar years. The exception is 2022,
where the edge is +1.1 points and insignificant -- in a sustained downtrend
call strikes are not tested at all, so there is nothing for the filter to
find. It is not decaying: 2026 is the strongest year in the sample at +29.2.

**Only the call side is used, and that is a finding rather than a shortcut.**
The identical test on puts returns the WRONG SIGN and no significance: -1.6
points at 1.13 sigma, z = -1.08. The asymmetry is mechanical. Upside is a
grind that walks up through the profile, so a call strike sitting inside the
traded band is directly in the path. Downside gaps over the whole profile in
one or two sessions, so where the value area sits tells you nothing about it.
Applying this filter to puts would be decoration.

**What the signal actually is.** Because distance is matched, "call strike
inside the value area" means VAH is above the strike -- the value area extends
well above spot, i.e. price is sitting in the lower part of its own recent
traded range. The filter therefore declines to sell upside into a market that
has been trading higher than it currently is. That is the mechanism, stated
plainly, and it is why this is a profile measurement and not a delta one.

Pure functions, zero I/O, in the style of `gates.py` and `regime.py`. Nothing
here reaches an order: it can only REMOVE a call strike from consideration,
never add one, never widen a band, and never size anything.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Sequence

from . import config as C
from .models import Bar


@dataclass(frozen=True)
class Profile:
    """One underlying's traded distribution over the lookback window."""
    underlying: str
    poc: float
    vah: float
    val: float
    bars: int
    source: str                       # "measured" | "degraded"
    notes: str

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("poc", "vah", "val"):
            d[k] = round(getattr(self, k), 2)
        return d


def degraded(underlying: str, why: str) -> Profile:
    """No profile. Every threshold is NaN-free but the source says so, and
    `call_strike_ok` passes everything -- unknown is not a veto."""
    return Profile(underlying=underlying, poc=0.0, vah=0.0, val=0.0,
                   bars=0, source="degraded", notes=why)


def value_area(underlying: str, bars: Sequence[Bar],
               bins: int = C.PROFILE_BINS,
               va_pct: float = C.VALUE_AREA_PCT) -> Profile:
    """POC, VAH and VAL from OHLCV.

    Daily bars do not carry an intraday price distribution, so each bar's
    volume is spread UNIFORMLY across its own high-low range before binning.
    That is the standard OHLCV approximation to a volume profile and it is an
    approximation -- it assumes a session traded its range evenly, which no
    session does. It is used because it is monotone in the thing that matters
    (where volume accumulated) and because the alternative, minute bars over
    a 20-day window for three names every cycle, is a data bill the agent
    does not need to pay for a 10-point effect.
    """
    if len(bars) < C.PROFILE_MIN_BARS:
        return degraded(underlying, f"only {len(bars)} bars, need "
                                    f"{C.PROFILE_MIN_BARS}")
    lo = min(b.low for b in bars)
    hi = max(b.high for b in bars)
    if not hi > lo:
        return degraded(underlying, "window has no range")
    width = (hi - lo) / bins
    vol = [0.0] * bins

    for b in bars:
        if b.volume <= 0:
            continue
        a, z = b.low, b.high
        if not z > a:                       # a bar that never moved
            i = min(int((a - lo) / width), bins - 1)
            vol[i] += b.volume
            continue
        # Overlap of [a, z] with each bin, in bin units. Sums to the bar's
        # own volume regardless of how many bins it spans.
        first = max(0, min(int((a - lo) / width), bins - 1))
        last = max(0, min(int((z - lo) / width), bins - 1))
        span = z - a
        for i in range(first, last + 1):
            blo = lo + i * width
            overlap = min(z, blo + width) - max(a, blo)
            if overlap > 0:
                vol[i] += b.volume * overlap / span

    total = sum(vol)
    if total <= 0:
        return degraded(underlying, "no volume in window")

    poc_i = max(range(bins), key=lambda i: vol[i])
    want = total * va_pct
    lo_i = hi_i = poc_i
    got = vol[poc_i]
    # Expand outward from the POC, always taking the heavier neighbour, until
    # the band holds va_pct of the volume. Ties expand upward, which is the
    # conservative direction for a filter that only ever vetoes call strikes.
    while got < want and (lo_i > 0 or hi_i < bins - 1):
        dn = vol[lo_i - 1] if lo_i > 0 else -1.0
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1.0
        if up >= dn:
            hi_i += 1
            got += up
        else:
            lo_i -= 1
            got += dn

    mid = lambda i: lo + (i + 0.5) * width
    return Profile(
        underlying=underlying, poc=mid(poc_i), vah=mid(hi_i), val=mid(lo_i),
        bars=len(bars), source="measured",
        notes=(f"{len(bars)}d profile, POC {mid(poc_i):.2f}, value area "
               f"{mid(lo_i):.2f}-{mid(hi_i):.2f} at {va_pct:.0%} of volume"),
    )


def call_strike_ok(strike: float, profile: Profile | None) -> bool:
    """True if a short CALL at this strike is clear of the traded band.

    A degraded or absent profile passes everything. The filter's job is to
    remove strikes it can prove are busy, and an unread profile proves
    nothing -- failing closed here would silently stop the agent selling
    calls on any cycle the bar endpoint hiccuped.
    """
    if profile is None or profile.source != "measured":
        return True
    return strike > profile.vah + C.VALUE_AREA_BUFFER
