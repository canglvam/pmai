# PMAI — Polymarket AI Prediction Market Monitor

AI-powered prediction market analysis agent. Automatically scans Polymarket for mispriced events, analyzes with DeepSeek/Claude, and recommends trades. Built for the [Agora Agents Hackathon](https://agora.thecanteenapp.com/) (May 11–25, 2026) on Arc + Circle.

## What It Does

1. **Market Discovery** — Fetches top Polymarket markets by volume, filters noise
2. **News Intelligence** — Searches Google News for each event via Serper API
3. **AI Analysis** — DeepSeek (or Claude) judges true probability vs market price
4. **Dual Review** — Secondary AI review checks settlement rules before acting
5. **Auto Settlement** — Polls Polymarket Gamma API to auto-resolve past trades
6. **Experience Loop** — Historical results feed back into future prompts

## Stack

| Layer | Technology |
|-------|-----------|
| AI Models | DeepSeek V4 Flash/Pro, Claude Opus 4.6/Sonnet 4.6 |
| Data | Polymarket Gamma API + CLOB API |
| News | Serper API (Google News) |
| **Settlement** | **Circle Developer-Controlled Wallets (Arc Testnet)** |
| Chain | Arc Testnet (Chain ID 5042002) — USDC gas, sub-1s finality |
| Notifications | Telegram Bot |

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your API keys
python main.py --once   # test run
python main.py          # continuous monitor (every 30 min)
```

## Circle Wallet Integration

The agent uses Circle Developer-Controlled Wallets on Arc testnet for USDC custody:

1. Register at https://console.circle.com → API Keys (SANDBOX)
2. Generate and register Entity Secret
3. Set `CIRCLE_API_KEY` and `ENTITY_SECRET` in `.env`

Without Circle config, the agent falls back to simulation mode.

## Configuration Highlights

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ACTIVE_MODEL` | `deepseek-v4-flash` | AI model (swap to `claude-opus` for stronger) |
| `PROBABILITY_EDGE_THRESHOLD` | `0.12` | Min price gap for action (12%) |
| `MAX_BET_PER_TRADE_USD` | `50` | Max per-trade size |
| `DAILY_BET_LIMIT_USD` | `200` | Daily betting cap |
| `DAILY_LOSS_LIMIT_USD` | `100` | Daily loss limit (pauses above) |
| `AUTO_TRADE_ENABLED` | `False` | False = paper trade only |

## Project Structure

```
├── main.py                  # Scheduler & scan orchestrator
├── config.py                # All parameters
├── polymarket_client.py     # Polymarket Gamma + CLOB APIs
├── news_searcher.py         # Google News via Serper API
├── claude_analyzer.py       # AI analysis core (DeepSeek/Claude)
├── auto_review.py           # Auto-settlement checker
├── experience_manager.py    # Historical record system
├── trader.py                # Trade execution + risk controls
├── circle_wallet.py         # Circle Dev Wallets on Arc testnet
├── notifier.py              # Telegram/Email alerts
├── data/experience_log.json # All analysis records
└── logs/                    # Runtime logs
```

## Roadmap

- [x] AI market analysis with dual review
- [x] Auto-settlement tracking
- [x] Circle Developer Wallet integration (Arc testnet)
- [x] Daily risk limits
- [ ] Kelly Criterion position sizing (waiting for settlement data)
- [ ] Cross-market correlation detection
- [ ] Live trading with stops (py-clob-client)
- [ ] Circle Gateway for cross-chain profit aggregation
- [ ] USYC integration for idle capital yield
- [ ] Web dashboard for public transparency

## License

MIT
