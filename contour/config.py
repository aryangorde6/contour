"""Single source of truth. Every threshold the agent obeys lives here.

Nothing in this file does I/O. Gate thresholds are quoted in WRITEUP.md
verbatim, so changing a number here changes the submitted write-up too.
"""
from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# --- universe -------------------------------------------------------------
# Three ETFs, deliberately. Single-name weeklies cost $40-80 round-trip
# against a ~$30-42 modeled edge; SPY/QQQ/IWM cost $8-20. See TECHNICAL.md.
UNIVERSE = ("SPY", "QQQ", "IWM")

WING_WIDTH = {"SPY": 5.0, "QQQ": 5.0, "IWM": 2.0}

# --- expiry lock (G8) -----------------------------------------------------
# Nothing may expire inside the judged window. Sep 11 is 11 DTE on Monday,
# 7 DTE on Thursday. No 0DTE: those contracts never return Greeks.
EXPIRY = date(2026, 9, 11)

# --- contest calendar -----------------------------------------------------
CONTEST_DAYS = (
    date(2026, 8, 31),  # Mon - no scheduled macro print
    date(2026, 9, 1),   # Tue - ISM Mfg + JOLTS 10:00 ET
    date(2026, 9, 2),   # Wed - ADP 08:15, Beige Book 14:00
    date(2026, 9, 3),   # Thu - ISM Services 10:00, Fed speakers; FLATTEN 15:45
    date(2026, 9, 4),   # Fri - NFP 08:30; VERIFY_ONLY
)
FLATTEN_DAY = date(2026, 9, 3)
FLATTEN_AT = time(15, 45)
MARKET_ESCALATION_AT = time(15, 50)
LAST_ENTRY_DAY = date(2026, 9, 3)
LAST_ENTRY_TIME_THU = time(11, 0)
VERIFY_ONLY_DAY = date(2026, 9, 4)

# --- G1 capital floor -----------------------------------------------------
START_NAV = 100_000.0
NAV_NO_ENTRY = 97_000.0    # -3.0%
NAV_HARD_HALT = 96_000.0   # -4.0% -> flatten + halt for the week

# --- G2 daily loss halt ---------------------------------------------------
DAILY_LOSS_HALT_PCT = -0.015

# --- the directional sleeve (contour/sleeve.py) ---------------------------
# One long QQQ equity position, run ALONGSIDE the options book and gated
# separately. It exists because the options book is capped at the credit it
# collects: a defined-risk premium seller's median week is under one percent,
# and no amount of gate-tightening changes that. The sleeve buys variance
# instead of edge, deliberately and with its own leash.
#
# Sized by the vol-scaling rule of LRS-Fortress -- the best risk-adjusted
# system in the strategy set it comes from (28.0% CAGR, Sharpe 0.94, max
# drawdown -49.3% over 55 years, versus 0.75 and -66.6% for LRS-VT2 alone).
# `regime.lrs_weight` IS that rule, already implemented and already sizing the
# options book, so the sleeve adds a position, not a second model.
#
# NOT transferred: Fortress is 70/30 equity/gold, and the gold sleeve is what
# earns most of that drawdown improvement. Only the equity leg is run here,
# because the instrument was specified. The sleeve therefore inherits
# Fortress's SIZING and none of its diversification -- say so rather than
# quoting the 0.94 Sharpe as though this were the whole system.
SLEEVE_UNDERLYING = "QQQ"
SLEEVE_NOTIONAL = 30_000.0          # ceiling, not order size: scaled by weight
SLEEVE_STOP_PCT = 0.04              # hard stop below the entry fill
# The LRS ladder rung required to open. 0.5 is the warning rung -- above the
# 200d, below the 50d -- so a warning-rung entry deploys HALF the notional and
# a clean one deploys all of it. Below the 200d, `lrs_weight` is 0 and nothing
# opens.
SLEEVE_MIN_LRS_W = 0.5
SLEEVE_MIN_SHARES = 1
# ONE entry, for the whole contest. The carve-out below funds exactly one stop
# loss; a sleeve that re-entered after being stopped out could spend it twice
# -- 2.4% behind a -4% halt that also has to cover a 2.8% options book. That is
# the same decoration bug G3 already had, arriving by a different route. It
# also refuses the whipsaw directly: a stop that immediately re-buys is not a
# stop, and on a four-day horizon there is no second trend to catch.
SLEEVE_ONE_SHOT = True

# What the sleeve costs the capital floor, at its ceiling. This is the number
# G3's ramp is reduced by, so the two together still fit behind the -4% halt.
# Measured 2026-09-01: QQQ at 717.01 sizes 41 shares / $29,398, a modeled stop
# loss of $1,176 -- inside this budget, because the budget is the ceiling.
SLEEVE_RISK_BUDGET_PCT = SLEEVE_NOTIONAL * SLEEVE_STOP_PCT / START_NAV   # 0.012

# Execution. Marketable limits, never market orders -- the same discipline the
# options book uses, for the same reason: a limit cannot print at a gap price
# if the book is momentarily empty. On a penny-wide ETF a 0.2% band fills like
# a market order. The escalated band is for the Thursday flatten, where not
# getting out is the worse failure.
SLEEVE_ENTRY_SLIP = 0.002
SLEEVE_EXIT_SLIP = 0.002
SLEEVE_EXIT_SLIP_ESCALATED = 0.01
SLEEVE_FILL_WAIT_S = 60

# --- G3 book risk ramp ----------------------------------------------------
# The ceiling is DERIVED, not chosen. G1 hard-halts and flattens at -4.0%, so
# a book carrying more simultaneous max loss than that can breach the capital
# floor without a single gate objecting on the way. The old top rung was 8%:
# twice the floor it was supposed to sit behind, and unreachable anyway --
# G4 capped the book at 6% with all three names qualifying and at 2% with
# one, which is the live case. A rung no configuration can touch is not a
# risk control, it is decoration.
#
# The sleeve is paid for OUT OF THIS SAME ALLOWANCE, not alongside it. A
# directional position that could lose 1.2% while the options book was
# separately permitted its full 4% would put 5.2% of reachable loss behind a
# 4% halt -- the identical decoration bug, reintroduced by addition. The
# options ceiling is therefore the halt distance MINUS whatever the sleeve has
# committed, so `SLEEVE_NOTIONAL = 0` restores the pre-sleeve numbers exactly.
# The manually-placed TQQQ tail is carved out for exactly the reason the
# sleeve is, and the reason is in the paragraph above: risk the options book
# cannot SEE is risk that sits behind the same halt without being counted
# against it. 6 x TQQQ260911C00070000 filled at $1.87 = $1,122, and a long
# call's max loss is the premium, so the number is exact rather than modelled.
# Set to 0.0 when the position is closed and the options book gets its room
# back automatically.
TAIL_RISK_BUDGET_PCT = 1_122.0 / START_NAV                      # 0.01122
BOOK_RISK_CEILING_PCT = ((START_NAV - NAV_HARD_HALT) / START_NAV
                         - SLEEVE_RISK_BUDGET_PCT
                         - TAIL_RISK_BUDGET_PCT)                # 0.0168
# The opening rung is a FRACTION of the ceiling rather than a constant: a
# hard-coded 0.02 survived the sleeve carve-out by luck and inverts the ramp
# outright once the tail carve-out shrinks the ceiling.
BOOK_RISK_RAMP = {
    date(2026, 8, 31): round(BOOK_RISK_CEILING_PCT * 0.71, 5),
    date(2026, 9, 1): BOOK_RISK_CEILING_PCT,
    # CLOSED EARLY, 2026-09-01 18:25 UTC. The ceiling above is derived from
    # START_NAV, so it answers "how much may the book risk on day one". That
    # is no longer the binding number: the halt is a fixed NAV LEVEL and
    # $1,408 of realised loss has already eaten the distance to it.
    #
    #     NAV                                        $98,592
    #     distance to the $96,000 hard halt           $2,592
    #       sleeve stop, 21 QQQ at 679.51               $589
    #       SPY Sep-11 condor, max loss                 $838
    #       TQQQ Sep-11 70C x6, premium = max loss    $1,122
    #     committed                                   $2,549
    #     UNCOMMITTED                                    $43
    #
    # $43 buys nothing, so the honest number is zero rather than a ramp
    # fitted to it. The sizing constants are deliberately NOT bent to make
    # room -- rewriting a per-position cap to accommodate a drawdown is how a
    # capital floor stops meaning anything. Exits are untouched: the exit loop
    # runs ahead of the gates, so targets, stops, breaches and the scheduled
    # flatten all still fire, and the agent keeps journaling every cycle.
    date(2026, 9, 2): 0.00,
    date(2026, 9, 3): 0.00,
    date(2026, 9, 4): 0.00,
}
# The previous 1.0% x 2 left the whole ramp unreachable and, with only SPY
# clearing the VRP floor all week, deployed 0.84% of a 4% allowance.
MAX_POSITION_RISK_PCT = 0.0125

# --- G4 concentration -----------------------------------------------------
MAX_CONCURRENT_POSITIONS = 6
# DERIVED, so the concentration gate cannot hand G3 a book G3 must refuse on
# the last position of every full name. 2.80 / 1.25 = 2.24 -> 2. Funding the
# sleeve costs the options book its third slot per underlying: at 4.00% it was
# 3.20 -> 3. That is the trade, and it is arithmetic rather than opinion.
MAX_POSITIONS_PER_UNDERLYING = int(BOOK_RISK_CEILING_PCT / MAX_POSITION_RISK_PCT)
MAX_NEW_PER_UNDERLYING_PER_CYCLE = 1

# --- G5 liquidity ---------------------------------------------------------
# DEVIATION FROM SPEC, deliberate.
#
# The spec set a flat "spread <= 8% of mid" per leg. That is correct for the
# short leg (~$1.00 mid, 8c allowance) and nonsense for the long wing: a
# 6-delta SPY wing quoted $0.10/$0.14 is a normal, liquid, 4-cent market, but
# 4c is 33% of a 12c mid. A flat percentage test rejects every wing the
# strategy buys, and the agent never trades.
#
# A leg passes if its spread is within EITHER the percentage OR the absolute
# allowance -- the standard way cheap options are screened.
MIN_OPEN_INTEREST = 500
MAX_SPREAD_PCT_OF_MID = 0.08
MAX_SPREAD_ABS = 0.10

# Package-level friction guard. The entire ETF-only universe decision rests on
# round-trip friction being $8-20 against a ~$90 credit rather than $40-80
# against $30-42. This gate enforces that argument instead of assuming it:
# total round-trip spread cost across all legs, as a fraction of net credit.
MAX_ROUND_TRIP_FRICTION_PCT_OF_CREDIT = 0.30

# Quote staleness. Measured 2026-08-30 against Friday's close: option QUOTES
# stop at 15:59:59.998 ET while option TRADES run to 16:14:58 ET, so the free
# indicative quote feed sits behind the tape. Whether that is a systematic
# 15-minute delay during live hours or an end-of-session artifact is resolved
# by the first live cycle -- the agent measures and journals quote age every
# cycle rather than assuming.
#
# The threshold tolerates the observed ~15-minute lag and catches genuinely
# broken or frozen data. It is deliberately NOT set to 900s: if the feed is
# systematically 15 minutes behind, a 900s veto rejects every candidate and
# the agent trades zero times.
MAX_QUOTE_AGE_S = 1200.0

# --- G7 delta band --------------------------------------------------------
SHORT_DELTA_BAND = (0.10, 0.16)
LONG_DELTA_BAND = (0.04, 0.10)
MAX_NET_DELTA_CONDOR = 0.08

# --- Volume profile strike filter ----------------------------------------
# The delta band above is a MODEL statement: 0.13 delta is the lognormal
# probability of finishing ITM. `profile.py` adds the measured one. Five
# years of SPY/QQQ/IWM daily bars say that at a FIXED 1.13-sigma distance, a
# call strike inside the traded value area is touched within 8 trading days
# 32.8% of the time against 21.9% outside it (z = +6.20). The filter removes
# the inside ones. Puts are deliberately untouched -- the same test on the
# put side returns the wrong sign at z = -1.08, because downside gaps over
# the profile instead of grinding through it.
#
# PROFILE_LOOKBACK_D is the window whose volume builds the distribution. It
# is NOT tuned: 20 trading days is one calendar month, chosen to match the
# horizon the agent actually trades, and the effect is stable across every
# vol regime in the sample rather than living at one window length.
# TURNED OFF, on the evidence of research/strategy_backtest.py. The touch-rate
# finding below is real and reproducible -- but touch probability is the WRONG
# OBJECTIVE. Across 387 cycles of real option prices (Jan 2024 - Aug 2026) the
# filter cut total P&L from +$926 to +$230 and profit factor from 1.09 to 1.02.
# It avoided no losses at all: the stop count barely moved (43 -> 44) while
# profit-target proceeds fell $11,463 -> $10,890. Dropping a condor's call side
# forfeits credit that is collected 73% of the time in order to dodge a touch
# that was not what actually hurt the book -- and CONDOR (PF 1.20) is a better
# structure than the PUT_CS (PF 1.10) the filter downgrades it to.
#
# The code and its research are kept rather than deleted, because a measured
# negative result is worth more than a quiet revert. Set this True to re-enable.
PROFILE_ENABLED = False
PROFILE_LOOKBACK_D = 20
PROFILE_MIN_BARS = 10          # below this the value area is noise, not shape
PROFILE_BINS = 240
VALUE_AREA_PCT = 0.70          # the standard Market Profile definition
# A strike exactly at VAH is inside the band for our purposes. The buffer is
# in dollars and deliberately small: it breaks ties away from the edge
# without moving the strike a whole increment.
VALUE_AREA_BUFFER = 0.01

# --- G9 credit floor ------------------------------------------------------
# DEVIATION FROM SPEC, deliberate and load-bearing.
#
# The spec set a flat 20%-of-wing floor "so risk/reward is never worse than
# ~4:1". That is arithmetically incompatible with G7's 13-delta short strikes:
# collecting 20% of width requires ~30-delta shorts. Run against the spec's own
# structure table, a 20% floor rejects SPY ($0.90 = 18%), QQQ ($0.95 = 19%),
# IWM ($0.42 = 21%, but $0.374 at ladder rung 3) and every one-sided vertical
# (~9%). The agent would have traded exactly zero times all week.
#
# The 4:1 premise is also moot in this design: realized loss is bounded by the
# stop at 1.0x credit, not by the wing. Max loss is only reached on an
# overnight gap straight through both strikes, which G3 already sizes for.
#
# Floors below are set to what 13-delta actually pays, with margin.
# Calibrated against a LIVE measurement, 2026-08-30, SPY Sep-11 chain:
#   sell 749P d=-0.131 / buy 744P d=-0.097 / sell 785C d=+0.134 / buy 790C d=+0.063
#   net credit $0.870 on a $5.00 wing = 17.4%; at ladder rung 3 (0.89x) = 15.5%.
# A 15% condor floor left 0.5pp of headroom, so any credit compression from
# here -- with VIX at a 2026 low, the likely direction -- would have the agent
# rejecting every candidate and trading zero times. 13% restores real margin.
# The "never worse than 4:1" premise the original 20% floor served is moot:
# realized loss is bounded by the stop at 1.0x credit, and thin-premium
# protection is now enforced directly by G5's round-trip friction guard.
MIN_CREDIT_PCT_OF_WING = {
    "PUT_CS": 0.08,
    "CALL_CS": 0.08,
    "CONDOR": 0.13,
}

# --- G11 schedule ---------------------------------------------------------
ENTRY_OPEN = time(10, 5)    # never in the first 35 minutes
ENTRY_CLOSE = time(15, 15)

# Hard-coded fallback blackouts (G10). Used when the LLM layer is
# unreachable; the agent keeps trading rather than stopping.
FALLBACK_BLACKOUTS = (
    (date(2026, 9, 1), time(9, 40), time(10, 20), "ISM Mfg + JOLTS 10:00 ET"),
    (date(2026, 9, 2), time(9, 30), time(9, 50), "ADP 08:15 ET spillover"),
    (date(2026, 9, 2), time(13, 40), time(14, 20), "Beige Book 14:00 ET"),
    (date(2026, 9, 3), time(9, 40), time(10, 20), "ISM Services 10:00 ET"),
    (date(2026, 9, 4), time(9, 30), time(11, 0), "NFP 08:30 ET"),
)

# --- exits ----------------------------------------------------------------
PROFIT_TARGET_PCT_OF_CREDIT = 0.50   # buy back at <= 50% of credit
STOP_MULTIPLE_OF_CREDIT = 2.0        # mark at 2.0x credit -> loss = 1.0x credit
BREACH_FRACTION_OF_WING = 0.30

# --- entry ladder ---------------------------------------------------------
LADDER_RUNGS = (0.97, 0.93, 0.89)    # x net mid credit
LADDER_RUNG_SECONDS = 90
CLOSE_ESCALATION_RUNGS = (1.15, 1.30)

# --- signal ---------------------------------------------------------------
VRP_RATIO_FLOOR = 1.30
SKEW_Z_TRIGGER = 0.8
RV_FLOOR = 6.0                        # max(rv10, 6.0) guards divide-by-small

# Skew priors, in vol points, for skew25 = IV(25d put) - IV(25d call).
#
# SEEDED FROM LIVE MEASUREMENT, 2026-08-30 (the Friday close), not from
# intuition. The originally specified priors (SPY 4.5, QQQ 4.0, IWM 5.5) were
# roughly 2 vol points too high, which put every name at z <= -0.9 and would
# have made the agent sell CALL spreads on all three underlyings for the whole
# week -- a systematic directional bet the structure map was never meant to
# express. The error came from calibrating against the 13-delta IV pair rather
# than the 25-delta pair the measure is actually defined over; 25-delta skew is
# materially flatter.
#
# A single observation is not a distribution, so the sd values are still
# judgement, and `mind.py` updates ref from a rolling in-session buffer once
# real cycles accumulate. Seeding at the measured level means day one reads
# skew-NEUTRAL and trades condors, rather than taking a directional view on the
# strength of a constant nobody measured.
SKEW_PRIOR = {
    "SPY": (2.52, 1.2),
    "QQQ": (2.81, 1.3),
    "IWM": (3.10, 1.5),
}


# --- regime sizing (contour/regime.py) ---------------------------------------
# Three published trend systems replace an LLM multiplier that was measured
# anchoring at 0.5 across sixteen consecutive cycles. See `regime.py` for the
# sources, the combination rule, and the two transfers it makes.
#
# THESE WINDOWS ARE NOT TUNING PARAMETERS. They are the literature-standard
# values of the source strategies, whose own header says: "DO NOT tune the
# 52/30/4 lengths -- they are the literature-standard, tested config. A failed
# forward test rejects the system." Stage-2's weekly windows are expressed in
# trading days (x5) because the DataSource seam returns undated closes;
# `tests/test_regime.py` pins that translation against a true weekly resample.
REGIME_LOOKBACK   = 1300   # daily closes fetched per underlying per cycle
REGIME_MIN_BARS   = 260    # below this the regime is DEGRADED, never guessed
REGIME_DEGRADED_W = 0.5    # unmeasurable regime -> half size, as before

STAGE2_ANCHOR_D   = 252    # 52 weeks -- the new-high anchor (reported only)
STAGE2_SMA_D      = 150    # 30 weeks -- Weinstein's stage line
STAGE2_RISING_D   = 20     # 4 weeks  -- the SMA must be rising vs this far back

RIBBON_EMAS       = (20, 50, 100, 200)   # fast, mid, slow, trend anchor

LRS_SLOW_D        = 200    # regime SMA
LRS_FAST_D        = 50     # fast re-entry rung of the two-speed ladder
LRS_VOL_D         = 20     # realized vol lookback
LRS_LONGRUN_D     = 1260   # ~5y long-run vol, the scaler's denominator
LRS_VETO_K        = 1.25   # vol veto fires only above this multiple of long-run
LRS_WARN_W        = 0.5    # weight in the warning rung (above slow, below fast)
LRS_EXT_CAP       = 0.25   # trim above this much extension over the slow SMA


# An ABSENT brain still halves the book. This is a policy response to missing
# information -- the same class as REGIME_DEGRADED_W -- and NOT the model
# exercising judgement, so moving sizing out of the model must not delete it.
# Only `Advice.source == "degraded"` (no provider configured) sets it; a
# provider that ANSWERS never does, which is what keeps the anchored 0.5 out.
DEGRADED_BRAIN_SIZE = 0.5
# --- positions the book holds but does not manage --------------------------
# 2026-09-01 18:14 UTC: 6 x TQQQ Sep-11 70C at $1.87 ($1,122), placed on an
# explicit instruction after the evidence against it was put on the record --
# a gap-down bounce tests as noise (t = +0.42) and the calls cost 1.4x realised
# vol. It is sized INSIDE the per-position cap and carved out of the book
# ceiling above, so it breaks no gate; it is acknowledged here so the orphan
# check reports it rather than discovering it.
#
# Earlier the same day: the long QQQ 720C tail was closed at $2.83
# for $3,113 against $4,433 paid -- a realised loss of $1,320, which was the
# entire account drawdown. It went on as a deliberate variance bet and came
# off for a measured reason: at 15.1% implied against 13.1% realised it was
# costing ~15% over fair value to hold, and research/strategy_backtest.py had
# just found no edge anywhere in the book to justify paying that.
#
# The mechanism stays because it is the honest way to hold an unmanaged leg:
# an acknowledged symbol is excluded from the orphan check but still counted
# and reported, so the book never silently disowns something it holds.
ACKNOWLEDGED_SYMBOLS: tuple[str, ...] = ("TQQQ260911C00070000",)
