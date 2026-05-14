# PMAI — Polymarket AI Prediction Market Monitor

## Overview
Automated Polymarket scanning → news search → AI pricing analysis → trade recommendations → auto settlement. Uses news information asymmetry to find undervalued events where market pricing lags.

---

## Project Structure

```
pmai/
├── main.py              # Entry point & scheduler: scan flow + cron
├── config.py            # All parameters (thresholds, models, API keys)
├── polymarket_client.py # Polymarket Gamma API + CLOB API client
├── news_searcher.py     # News search (Serper API, Google News)
├── claude_analyzer.py   # AI analysis core (DeepSeek/Claude API)
├── auto_review.py       # Auto-review module (detects settled markets, updates results)
├── experience_manager.py # Experience accumulation (persists historical decisions)
├── trader.py            # Trade execution (paper trade + live orders + risk controls)
├── circle_wallet.py     # Circle Developer Wallets (Arc testnet settlement)
├── notifier.py          # Notifications (Telegram Bot / Email)
├── README.md            # Project overview (English)
├── SYSTEM.md            # This file
├── requirements.txt     # Python dependencies
├── .env                 # API keys (not committed to Git)
├── data/
│   └── experience_log.json  # All analysis records (core data file)
└── logs/
    ├── monitor.log      # Runtime logs (full records per scan)
    └── trade_log.json   # Trade execution log
```

---

## Full Scan Flow (~35-60 seconds)

```
Step 0: Auto Review
  → Iterate experience_log records where result=null
  → Query Gamma API to check if market is settled
  → If settled → determine WIN/LOSS → calculate PnL → auto-update record

Step 1: Fetch Markets
  → Query Gamma API for top 30 active markets by volume
  → Filter out markets with 24h volume < $10,000
  → Dedup: skip markets analyzed within last 1 hour (catches breaking news)
  → Extreme price filter: skip YES > 99% or < 1% (settled/decided)
  → Take top 10 for analysis
  → 3 retries with incremental waits: 5s, 10s, 15s

Step 2: Search News
  → For each market, query Serper API for Google News (English)
  → Returns title, snippet, source, date, link
  → 0.5s interval between requests to avoid rate limiting

Step 3: AI Batch Analysis
  → Build prompt per market: market info + news + historical experience
  → Call DeepSeek V4 Flash API, get JSON response
  → JSON contains: probability estimate, confidence, action, reasoning
  → 1s interval between requests

Step 3.5: Dual Review
  → For all markets where action != SKIP
  → Re-read full settlement rules (Gamma API description field)
  → AI secondary review: rule conflicts? missed edge cases?
  → Three verdicts: CONFIRM / ADJUST / OVERRIDE

Step 4: Execute Decisions
  → Triple filter:
    1. action == SKIP → skip
    2. |probability edge| < 12% → skip (edge too thin)
    3. confidence != HIGH → skip (uncertain)
  → Passed → paper trade (PAPER_TRADE) or live order (if configured)
  → Record to experience_log.json
  → Generate scan report → log output
```

---

## Core Parameters (config.py)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `ACTIVE_MODEL` | `deepseek-v4-flash` | AI model. Also: deepseek-v4-pro / claude-opus / claude-sonnet |
| `MONITOR_INTERVAL_MINUTES` | 30 | Scan interval in continuous mode |
| `MAX_MARKETS_TO_ANALYZE` | 10 | Max markets per scan |
| `MIN_VOLUME_USD` | 10000 | 24h volume threshold (USD) |
| `PROBABILITY_EDGE_THRESHOLD` | 0.12 | Market vs AI gap > 12% to act |
| `MAX_BET_PER_TRADE_USD` | 50 | Max bet per trade |
| `AUTO_TRADE_ENABLED` | False | False=paper trade, True=live orders |
| `DAILY_BET_LIMIT_USD` | 200 | Daily total bet cap |
| `DAILY_LOSS_LIMIT_USD` | 100 | Daily loss cap (pauses above) |
| `CIRCLE_WALLET_ENABLED` | auto-detect | Creates Arc agent wallet when CIRCLE_API_KEY configured |
| `AI_MAX_TOKENS` | 2000 | Max AI output tokens |
| `temperature` | 0.3 | AI analysis temperature (low = consistent) |

---

## Cost Estimates

| Item | Unit Price | Per Scan | Daily (48 scans) |
|------|-----------|----------|-------------------|
| DeepSeek V4 Flash (input) | $0.014/M tokens | ~3k tokens × 10 markets | ~$0.02 |
| DeepSeek V4 Flash (output) | $0.28/M tokens | ~500 tokens × 10 markets | ~$0.07 |
| DeepSeek V4 Flash (review) | $0.28/M tokens | ~300 tokens × 3 markets | ~$0.01 |
| Serper API | $50/month 2500 queries | ~10 queries/scan | ~480/day |

**Monthly total: ~$4-5**

---

## Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Single scan
python main.py --once

# Continuous monitoring (every 30 min)
python main.py
# Press Ctrl+C to stop

# Manually update a trade result
python main.py --update-result <record_id> <WIN/LOSS/PUSH> <pnl_usd> <lesson>
```

---

## Required API Keys

| API | Env Variable | How To Get |
|-----|-------------|-------------|
| DeepSeek | `DEEPSEEK_API_KEY` | platform.deepseek.com |
| Serper (news search) | `SERPER_API_KEY` | serper.dev |
| Circle Wallets (agent treasury) | `CIRCLE_API_KEY` + `ENTITY_SECRET` | console.circle.com → SANDBOX |
| Telegram notifications (recommended) | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | @BotFather |
| Polymarket wallet (live trading only) | `POLYMARKET_PRIVATE_KEY` | MetaMask private key |

---

## Data Record Format (experience_log.json)

```json
{
  "id": 1,
  "timestamp": "2026-05-10T10:38:32",
  "market_id": "1919425",
  "market_question": "UFC 328: Sean Strickland vs. Khamzat Chimaev",
  "market_yes_price": 0.195,
  "claude_probability": 0.40,
  "probability_edge": 0.205,
  "claude_reasoning": "Market underestimates Strickland's win probability...",
  "recommended_action": "BUY_YES",
  "action_taken": "Paper trade: BUY_YES $50",
  "bet_amount_usd": 0,
  "result": null,
  "pnl_usd": null,
  "lesson": null
}
```

- `result` = null means pending settlement; auto-review polls until resolved
- `result` filled → contributes to win rate statistics
- `market_id` is the key field for auto-review lookup

---

## Key Design Decisions

1. **1-hour dedup**: balances API cost with timely breaking news capture
2. **Extreme price filter**: skip markets at >99% or <1% — already settled, no edge
3. **Dual review**: re-check settlement rules after initial analysis to catch rule traps (proven effective: corrected tennis walkover rules, soccer 90-minute limits, etc.)
4. **12% threshold**: mathematically derived. 9% edge at mid-price yields only 18% ROI, ~15% after slippage — too thin
5. **Flash model**: deepseek-v4-flash is 3.3x faster, 10x cheaper, zero disconnects vs pro; analysis quality difference negligible for structured JSON tasks
6. **Circle Wallet integration**: agent USDC treasury via Circle Developer Wallets (Arc testnet), on-chain verifiable
7. **Daily risk controls**: $200 bet limit + $100 loss limit prevents runaway trades
