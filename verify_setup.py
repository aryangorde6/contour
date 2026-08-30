"""Pre-flight check. Run this the moment you have API keys.

Answers the questions that decide the whole architecture:
  - do the keys work, is it paper, is the balance exactly $100,000
  - what options level is the account (need 3 for spreads)
  - can we list option contracts
  - can we get option QUOTES and GREEKS on a free paper account
That last one is the big unknown -- if greeks are not served, we compute
them locally and the design has to account for it.
"""
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

USE_DEV = "--dev" in sys.argv
prefix = "ALPACA_DEV_" if USE_DEV else "ALPACA_"
key = os.getenv(prefix + "API_KEY")
sec = os.getenv(prefix + "SECRET_KEY")

if not key or not sec:
    sys.exit(f"missing {prefix}API_KEY / {prefix}SECRET_KEY in .env")

print(f"=== checking {'DEV' if USE_DEV else 'COMPETITION'} account ===\n")

ok, fail = [], []


def check(name):
    def deco(fn):
        try:
            result = fn()
            print(f"[ok]   {name}: {result}")
            ok.append(name)
        except Exception as e:
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            fail.append(name)
    return deco


from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType, AssetStatus

trading = TradingClient(key, sec, paper=True)
acct = [None]


@check("account")
def _():
    a = trading.get_account()
    acct[0] = a
    return (f"account_number={a.account_number} status={a.status} "
            f"equity=${float(a.equity):,.2f} buying_power=${float(a.buying_power):,.2f}")


@check("balance is exactly $100,000")
def _():
    eq = float(acct[0].equity)
    if abs(eq - 100_000) > 0.01:
        raise ValueError(f"equity is ${eq:,.2f}, must be $100,000.00 -- reset it in the Alpaca dashboard")
    return "correct"


@check("options level")
def _():
    lvl = getattr(acct[0], "options_trading_level", None)
    appr = getattr(acct[0], "options_approved_level", None)
    if lvl is None:
        raise ValueError("no options_trading_level on account object")
    if int(lvl) < 3:
        raise ValueError(f"level {lvl} -- need 3 for multi-leg spreads")
    return f"trading_level={lvl} approved_level={appr} (3 = multi-leg spreads OK)"


contracts = [None]


@check("list option contracts (SPY)")
def _():
    exp_gte = date.today()
    exp_lte = date.today() + timedelta(days=45)
    req = GetOptionContractsRequest(
        underlying_symbols=["SPY"],
        status=AssetStatus.ACTIVE,
        expiration_date_gte=exp_gte,
        expiration_date_lte=exp_lte,
        type=ContractType.CALL,
        limit=100,
    )
    res = trading.get_option_contracts(req)
    contracts[0] = res.option_contracts
    n = len(contracts[0])
    sample = contracts[0][0].symbol if n else "none"
    return f"{n} contracts, e.g. {sample}"


from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, OptionLatestQuoteRequest

optdata = OptionHistoricalDataClient(key, sec)


@check("option chain snapshot + GREEKS/IV entitlement")
def _():
    chain = optdata.get_option_chain(OptionChainRequest(underlying_symbol="SPY"))
    n = len(chain)
    with_greeks = sum(1 for v in chain.values() if getattr(v, "greeks", None) is not None)
    with_iv = sum(1 for v in chain.values() if getattr(v, "implied_volatility", None) is not None)
    return (f"{n} strikes | greeks on {with_greeks} | IV on {with_iv}"
            + ("  <-- GREEKS SERVED, no local pricing needed" if with_greeks
               else "  <-- NO GREEKS, must compute locally (py_vollib)"))


@check("latest option quote (bid/ask liquidity)")
def _():
    if not contracts[0]:
        raise ValueError("no contracts from previous step")
    sym = contracts[0][len(contracts[0]) // 2].symbol
    q = optdata.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=sym))
    quote = q[sym]
    spread = quote.ask_price - quote.bid_price
    return f"{sym} bid={quote.bid_price} ask={quote.ask_price} spread={spread:.2f}"


@check("open positions / orders (should be empty on a fresh account)")
def _():
    pos = trading.get_all_positions()
    if pos and not USE_DEV:
        raise ValueError(f"{len(pos)} open positions -- competition account must be untouched")
    return f"{len(pos)} positions"


print(f"\n=== {len(ok)} passed, {len(fail)} failed ===")
if acct[0]:
    # lablab wants the PA-prefixed account NUMBER, not the internal UUID.
    # Four of the 23 current submissions typed garbage here and have already
    # forfeited P&L verification.
    print(f"\nSUBMIT THIS TO LABLAB (account_number): {acct[0].account_number}")
    print(f"  (internal UUID, NOT the one to submit: {acct[0].id})")
if fail:
    print("failed:", ", ".join(fail))
    sys.exit(1)
