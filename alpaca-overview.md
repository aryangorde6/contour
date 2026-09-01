# Alpaca AI Trading Agents Hackathon

**Source:** [lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)

**Tagline:** Code the next generation of algorithmic trading

## At a glance

| | |
|---|---|
| **Format** | Fully online |
| **Dates** | 28 August – 4 September 2026 |
| **Prize pool** | ~$5,000–$6,000 |
| **Teams** | 1–6 people |
| **Eligibility** | 18+, worldwide |
| **Track** | Options Alpha Agents |

Build autonomous AI trading agents and trading apps using Alpaca’s Trading API, MCP server, and CLI. Everything runs in Alpaca’s paper trading environment (simulated funds, real market data).

## Prizes

| Place | Amount |
|---|---|
| 1st | $2,500 |
| 2nd | $1,500 |
| 3rd | $1,000 |
| Social engagement | 2 awards |

## What you must build

- An **autonomous AI trading agent** that can actually place trades
- Using **Alpaca Trading API** plus **MCP server and/or CLI**
- Strategy **must include options trading**
- Paper trading only for the competition account

## Submission requirements

1. **Dedicated competition paper account** (one per email)
2. Competition account **starting balance set to $100,000**
3. **One-page write-up** covering:
   - AI logic
   - Risk gates
   - Alpaca infrastructure implementation
4. All strategies must **include options trading**

## Judging criteria

- P&L performance
- Technology implementation
- Creativity & originality
- Presentation & execution

## Allowed / expected stack

- Alpaca Trading API
- Alpaca MCP Server
- Alpaca CLI
- Alpaca paper trading environment
- Python, GitHub, common AI agent tooling

## Live dashboard (snapshot)

From the [live results page](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live):

- ~3,300 participants
- ~1,000 teams
- Submissions open; Options Alpha Agents is the active track
- Common tools in use: Alpaca, Claude Code, Anthropic Claude, Gemini, Codex, Cursor, Streamlit, etc.

## Community & links

- **Sign up / enroll:** [Hackathon page](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon?enroll=true)
- **Live results:** [lablab.ai live dashboard](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live)
- **Discord:** [discord.gg/lablabai](https://discord.gg/lablabai)
- **Twitch:** [twitch.tv/lablabai](https://www.twitch.tv/lablabai)
- **Help center:** [lablab.ai/help-center](https://lablab.ai/help-center)

## Contour (this repo)

This repository implements **Contour** — an autonomous options agent aimed at this hackathon: measure the vol surface, pick structure (put/call credit spread, iron condor, or flat), trade SPY/QQQ/IWM. Writes go through the **Alpaca CLI**; reads go through the official **`alpaca-py`** SDK. Not MCP: its server cannot place multi-leg option orders ([alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97)), and the brief asks for "MCP server **and/or** CLI", which the CLI write path satisfies on its own. See `README.md` and `TECHNICAL.md`.
