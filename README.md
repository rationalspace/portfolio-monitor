# Portfolio Monitor (Broker)

Tier-aware portfolio alert system for a Broker brokerage account, running locally on macOS.
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

All assignments live in `tiers.yaml` (git-ignored — copy from `tiers.example.yaml`). No code changes needed to move a stock between tiers.

| Tier | Description | Protection level |
|---|---|---|
| `tier_1` | Blue-chip compounders — long-term core positions | Highest — no sell alerts, concentration watch only |
| `tier_2` | High-octane growth — strong conviction holds | High — alerts only on fundamental breakdown |
| `tier_3` | Specialized / rebound plays — active monitoring | Medium — weakness watch with drawdown + DMA gates |
| `tier_4` | Speculative growth — tighter leash | Tighter — lower drawdown threshold before alert fires |
| `exit_pool` | Positions decided to exit — hunt every rally | None — alerts on any meaningful pop or consecutive up days |
| `crypto_exposure` | Crypto-linked ETFs | Sell-into-strength only; fundamentals rules disabled |
| `watchlist` | Buy candidates — not yet fully positioned | Buy-the-dip alerts when quality names pull back |
| `index_fund` | Broad index funds | Excluded from all single-name rules |

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

### Schedule (macOS launchd)

Create a plist in `~/Library/LaunchAgents/com.portfoliomonitor.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.portfoliomonitor.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOUR_USERNAME/portfolio-monitor/.venv/bin/python</string>
    <string>-m</string>
    <string>portfolio_monitor.scripts.run_guarded</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/YOUR_USERNAME/portfolio-monitor</string>
  <key>StartCalendarInterval</key>
  <array>
    <!-- fires every 30 min, 4:30–8:00 PM ET (Mon–Fri) -->
    <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>17</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>/Users/YOUR_USERNAME/Library/Logs/portfolio-monitor.log</string>
  <key>StandardErrorPath</key><string>/Users/YOUR_USERNAME/Library/Logs/portfolio-monitor.log</string>
</dict>
</plist>
```

Then load it:
```bash
launchctl load ~/Library/LaunchAgents/com.portfoliomonitor.daily.plist
launchctl list com.portfoliomonitor.daily   # verify loaded
```

### Dry run
```bash
python -m portfolio_monitor.scripts.run_guarded --dry-run
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
cp tiers.example.yaml tiers.yaml   # then populate with your own tickers
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
  --account-map "<YOUR_ACCOUNT_1>=ghostfolio-account-id-1" \
  --account-map "<YOUR_ACCOUNT_2>=ghostfolio-account-id-2"
```

Dividends and unmapped accounts are automatically excluded.

## Project layout

```
portfolio-monitor/
├── tiers.yaml                  # Your tier assignments (git-ignored — copy from tiers.example.yaml)
├── tiers.example.yaml          # Template — populate with your own tickers
├── realized_pnl.yaml           # Closed position ledger (git-ignored — copy from realized_pnl.example.yaml)
├── config.yaml                 # Thresholds + runtime config (edit freely)
├── docker-compose.yml          # Optional Ghostfolio stack
├── .env.example                # Ghostfolio secrets template
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
└── tests/                      # Tests, no network required
```

## Tests
```bash
pytest tests/   # all offline
```

---

*This is a notification tool, not a financial advisor. Review tier assignments quarterly. Nothing here is investment, tax, or legal advice.*
