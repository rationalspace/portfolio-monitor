# Portfolio Monitor (US — Broker)

Tier-aware portfolio alert system for a Broker book, running locally on macOS.
Designed to *protect compounders, trim near all-time highs, prune laggards on rallies, and dip-buy quality* — not to chase momentum.

## Architecture

```
SnapTrade (Broker OAuth) ──► positions + lot-level data
                                    │
                                    ▼
   yfinance (prices, ATH, ─────► Alert engine (Python)  ──► Gmail SMTP
   fundamentals, news)              │                        (HTML alerts +
                                    ▼                         weekly digest)
                            launchd (4:30–8 PM ET, Mon–Fri)
                            Catch-up: fires on wake if missed
```

## The 10 rules

| # | Rule | Applies to | Fires when |
|---|---|---|---|
| 0a | **ATH Proximity** | Tier 1 + 2 | Stock grinds to ≥85% of ATH with long-term profitable lots — the "slow grind" exit signal |
| 0b | **Exit Watchlist** | Exit Pool | 3% day pop, 8% 5-day rally, or 3+ consecutive up days — low-threshold exit hunting |
| 1 | **Sell Into Strength** | Exit Pool + Crypto | 5%+ pop with volume confirmation, earnings gap-up, or news catalyst |
| 2 | **Capitulation** | Exit Pool | Loss >35% + fundamental deterioration (digest only) |
| 3 | **Tier 3 Weakness** | Tier 3 | Drawdown >15% + below 200-DMA + fundamentals signal |
| 4 | **Tier 4 Weakness** | Tier 4 | Drawdown >25% + below 200-DMA + fundamentals signal |
| 5 | **Buy The Dip** | Watchlist (not held) | Off-high ≤85% + RSI<40 + healthy fundamentals |
| 6 | **Top-Up Compounder** | Watchlist (held) | Existing Tier 1/2 name pulls back to ≤85% of 52w high |
| 7 | **Concentration Drift** | Tier 1 + 2 | Position exceeds 12% of portfolio (digest only) |
| 8 | **Earnings Heads-Up** | All | Earnings within 3 trading days (digest only) |

All sell-side alerts include:
- **Day % change + dollar value change** on the full position
- **Per-lot LTCG breakdown** — long-term profitable lots sorted first, with gain per share, total gain, and days held

## Tier system

All assignments live in `tiers.yaml` — no code changes needed to move a stock.

| Tier | Description | Example holdings |
|---|---|---|
| `tier_1` | Blue-chip compounders — protect | MSFT, NVDA, GOOGL, AAPL, AMZN |
| `tier_2` | High-octane growth — protect with light watch | META, PLTR, ANET, ORCL, NFLX |
| `tier_3` | Specialized / rebound — active watch | QCOM, NET, OKTA |
| `tier_4` | Speculative — tighter watch | SOFI |
| `exit_pool` | Decided to exit — hunt every rally | SMCI, S, RBRK, PYPL, PATH, OXY, ADBE, TWLO, DOCN |
| `crypto_exposure` | Crypto ETFs | COIN, FBTC |
| `watchlist` | Buy candidates not yet fully held | MU, TSM, KLAC, ARM, CRWV |
| `index_fund` | Excluded from single-name rules | QQQ, FXAIX, FBGRX |

## Scheduling & catch-up

- **launchd** fires every 30 min from 4:30–8:00 PM ET, Mon–Fri
- **Catch-up**: if the Mac was asleep at 4:30, the next wake fires immediately
- **Sentinel file** (`~/.portfolio-monitor-last-run`) ensures exactly one run per calendar day
- **7-day cooldown** per (symbol, rule) pair to prevent alert fatigue

## Setup

### Install
```bash
cd ~/portfolio-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Secrets (macOS Keychain)
```bash
python -c "import keyring; keyring.set_password('portfolio-monitor', 'SNAPTRADE_CLIENT_ID', 'YOUR_ID')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'SNAPTRADE_CLIENT_SECRET', 'YOUR_SECRET')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'SNAPTRADE_USER_ID', 'YOUR_USER_ID')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'SNAPTRADE_USER_SECRET', 'YOUR_USER_SECRET')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'gmail_address', 'you@gmail.com')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'gmail_app_password', 'xxxx-xxxx-xxxx-xxxx')"
```

### Dry run
```bash
python -m portfolio_monitor.scripts.run_guarded --dry-run
```

### Install launchd job
```bash
cp launchd/com.portfoliomonitor.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.portfoliomonitor.daily.plist
launchctl list com.portfoliomonitor.daily   # verify loaded
```

### Logs
```
~/Library/Logs/portfolio-monitor.log
```

## Configuration

Two YAML files drive everything — no code changes ever needed:

- **`tiers.yaml`** — tier assignments, watchlist, exit pool, per-ticker overrides *(git-ignored — copy from `tiers.example.yaml`)*
- **`config.yaml`** — rule thresholds, on/off switches, scheduler timing

```bash
cp tiers.example.yaml tiers.yaml   # then edit with your own tickers
```

A `realized_pnl.yaml` ledger tracks closed positions (also git-ignored). Copy from `realized_pnl.example.yaml` to start your own.

## Ghostfolio (optional dashboard)

A `docker-compose.yml` is included to run [Ghostfolio](https://ghostfolio.dev) locally — portfolio dashboard, lot store, charts.

```bash
cp .env.example .env          # fill in secrets (openssl rand -hex 32 for each)
docker compose up -d          # starts Ghostfolio + Postgres + Redis at localhost:3333
```

`.env` is git-ignored. Never commit it. `.env.example` is the template.

### Broker CSV → Ghostfolio import

```bash
python -m portfolio_monitor.scripts.broker_to_ghostfolio \
  input.csv output.csv \
  --account-map "<YOUR_ACCOUNT_ID>=ghostfolio-account-id-1" \
  --account-map "<YOUR_ACCOUNT_2>=ghostfolio-account-id-2"
```

Dividends and unmapped accounts are automatically excluded. The output CSV is a generated file — not committed to git.

## Project layout

```
portfolio_monitor/
├── tiers.yaml                  # Tier assignments (edit freely)
├── config.yaml                 # Thresholds + runtime config (edit freely)
├── launchd/
│   └── com.portfoliomonitor.daily.plist
├── portfolio_monitor/
│   ├── main.py                 # Daily orchestration
│   ├── tiers_loader.py         # YAML → typed config + AppConfig
│   ├── portfolio_types.py      # Position, Lot, Portfolio dataclasses
│   ├── snaptrade_client.py     # Live Broker holdings via SnapTrade
│   ├── market_data.py          # yfinance: prices, ATH, fundamentals, news
│   ├── store.py                # SQLite cooldown log
│   ├── email_dispatch.py       # Jinja2 HTML → Gmail SMTP
│   ├── rules/
│   │   ├── ath_proximity.py    # Rule 0a — ATH grind alert (Tier 1+2)
│   │   ├── exit_watchlist.py   # Rule 0b — momentum exit (Exit Pool)
│   │   ├── sell_into_strength.py
│   │   ├── capitulation.py
│   │   ├── tier_weakness.py
│   │   ├── buy_the_dip.py
│   │   ├── concentration.py
│   │   └── earnings.py
│   ├── templates/
│   │   ├── alert.html.j2       # Per-alert email (lot breakdown, day change)
│   │   └── digest.html.j2      # Weekly Saturday digest
│   └── scripts/
│       ├── run_guarded.py      # Time-gate + sentinel → main.run_once()
│       └── broker_to_ghostfolio.py
└── tests/                      # 77 tests, no network required
```

## Tests
```bash
pytest tests/   # 77 tests, all offline
```

---

*This is a notification tool, not a financial advisor. Review tier assignments quarterly. Nothing here is investment, tax, or legal advice.*
