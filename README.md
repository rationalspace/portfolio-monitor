# Portfolio Monitor

Tier-aware alert system for a brokerage account, running locally on macOS.
Designed to *protect compounders, trim near all-time highs, prune laggards on rallies, and dip-buy quality* — not to chase momentum.

Connects to your brokerage via [SnapTrade](https://snaptrade.com) (supports 50+ brokers).

## Architecture

```
SnapTrade (brokerage OAuth) ──► positions + lot-level data
                                    │
                                    ▼
   yfinance (prices, ATH, ─────► Alert engine (Python)  ──► Gmail SMTP
   fundamentals, news,              │                        (HTML alerts +
   MA50/MA200, Bol. Bands)          ▼                         weekly digest)
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
| 5 | **Buy The Dip** | Watchlist (not held) | Off-high ≤85% + RSI<40 + healthy fundamentals (PE ceilings apply) |
| 6 | **Top-Up Compounder** | Watchlist (held Tier 1/2) | Pullback to threshold — enriched with technical quality tier (see below) |
| 7 | **Concentration Drift** | Tier 1 + 2 | Position exceeds 12% of portfolio (digest only) |
| 8 | **Earnings Heads-Up** | All | Earnings within 3 trading days (digest only) |
| 9 | **MA Crossover** | Tier 1 + 2 | 50-day average crosses 200-day average — golden or death cross |
| 10 | **Akshat Trade Signal** | WisdomHatch watchlist | New buy/sell detected on Akshat's portfolio page — cross-referenced against your tiers and fundamentals |

### Rule 10 — Akshat Trade Signal

Scrapes https://wisdomhatch.com/akshat-us-portfolio/ after every run using a headless browser (Playwright + stealth mode to bypass CleanTalk anti-bot). New trades are detected via SQLite change-tracking (`akshat_trades.db`) — only first-time-seen trades generate alerts.

Severity logic:

| Condition | Severity |
|---|---|
| Akshat buys something on your Tier 1/2 watchlist | HIGH |
| Akshat sells something you currently hold | HIGH |
| New name (not on watchlist), fundamentals healthy | MEDIUM |
| Akshat buys Tier 3/4 name, or fundamentals fail PE/margin gates | DIGEST |

Credentials (`wisdomhatch_email`, `wisdomhatch_password`) are stored in macOS Keychain under the `portfolio-monitor` service — never in any file. Run `portfolio-monitor-bootstrap` to set or update them.

## Technical indicators

Every `PriceSnapshot` carries technical indicators at zero extra API cost:

| Field | Source | What it means |
|---|---|---|
| `ma50` | yfinance info dict (Yahoo pre-calculates) | 50-day simple moving average |
| `ma200` | yfinance info dict | 200-day simple moving average |
| `bb_upper` / `bb_lower` | 20-day Bollinger Bands (2σ), pandas rolling | Upper/lower bounds of the 20-day price range |
| `bb_pct_b` | `(price − bb_lower) / (bb_upper − bb_lower)` | 0 = at floor, 0.5 = mid, 1 = at ceiling |
| `above_ma50` / `above_ma200` | Boolean flags | Is price above the 50/200-day average? |

### Rule 6 — Top-Up Compounder quality tiers

Alerts are tiered by technical quality. Severity and title adapt to the setup:

| Tier | Condition | Severity | Example title |
|---|---|---|---|
| `SCREAMING_BUY` | Near BB lower (< 15%) **and** RSI oversold (< 42) | HIGH | "NVDA — Strong entry: at price floor with selling exhausted" |
| `STRONG_DIP` | At BB lower **or** deeply oversold (RSI < 35) | HIGH | "AVGO — Good entry: near the price floor, 13% off peak" |
| `HEALTHY_DIP` | Below threshold, above MA200, trend intact | MEDIUM | "MSFT — Standard entry: 9% off peak, long-term trend intact" |
| `TREND_CAUTION` | Below threshold but below MA200 (trend broken) | DIGEST | "QCOM — Threshold met but long-term trend is under pressure" |

**RSI gate**: suppresses alerts when RSI > 55 and price isn't near BB lower — the stock isn't in a real dip technically.

The email includes a visual gradient bar showing where price sits in its 20-day range, MA50/MA200 status pills, and a plain-English "What the chart is telling you" paragraph — no jargon.

### Rule 9 — MA Crossover

Detects the exact day the 50-day average crosses the 200-day average for held Tier 1/2 positions:

- **Golden cross** (MA50 crosses above MA200): `MEDIUM` — *"NVDA — Recovery momentum confirmed"*
- **Death cross** (MA50 crosses below MA200): `HIGH` — *"MSFT — Selling pressure is becoming a trend (not just a dip)"*

30-day cooldown prevents re-alerting when averages hover near each other. Plain-English body explains what the crossing means for a long-term hold (watch signal, not an exit trigger).

---

All sell-side alerts include:
- **Day % change + dollar value change** on the full position
- **Per-lot LTCG breakdown** — long-term profitable lots sorted first, with gain per share, total gain, and days held

## Tier system

All assignments live in `tiers.yaml` (kept local — copy from `tiers.example.yaml`). No code changes needed to move a stock between tiers.

| Tier | Description | Protection level |
|---|---|---|
| `tier_1` | Blue-chip compounders — long-term core positions | Highest — no sell alerts, concentration watch only |
| `tier_2` | High-octane growth — strong conviction holds | High — alerts only on fundamental breakdown |
| `tier_3` | Specialized / rebound plays — active monitoring | Medium — weakness watch with drawdown + DMA gates |
| `tier_4` | Speculative growth — tighter leash | Tighter — lower drawdown threshold before alert fires |
| `exit_pool` | Positions decided to exit — hunt every rally | None — alerts on any meaningful pop or consecutive up days |
| `crypto_exposure` | Crypto-linked ETFs | Sell-into-strength only; fundamentals rules disabled |
| `watchlist` | Buy candidates — dip-buy and top-up rules | Technical quality tiers on every alert |
| `index_fund` | Broad index funds | Excluded from all single-name rules |

### Per-ticker overrides (`tiers.yaml`)

```yaml
overrides:
  PLTR:
    top_up_off_high_threshold: 0.90   # Alert at 10% off high instead of default 15%
  MU:
    top_up_off_high_threshold: 0.80   # Cyclical — wait for a real 20% dip
```

## Scheduling & catch-up

- **launchd** fires every 30 min from 4:30–8:00 PM ET, Mon–Fri
- **Catch-up**: if the Mac was asleep at 4:30, the next wake fires immediately
- **Sentinel file** (`~/.portfolio-monitor-last-run`) ensures exactly one run per calendar day
- **7-day cooldown** per (symbol, rule) pair — MA Crossover uses 30-day cooldown

## Setup

### 1. Install
```bash
cd ~/portfolio-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. SnapTrade (brokerage connection)

SnapTrade is the OAuth bridge that connects this tool to your brokerage account. It's free for personal use.

1. Sign up at [snaptrade.com](https://snaptrade.com) and create an application to get a **Client ID** and **Consumer Key**
2. Run the one-time registration to get your **User ID** and **User Secret**:
   ```bash
   python -m portfolio_monitor.scripts.snaptrade_register
   ```
   This opens a browser to connect your brokerage account via OAuth.
3. Store the four credentials in macOS Keychain (see step 3 below)

### 3. Secrets (macOS Keychain)
```bash
python -c "import keyring; keyring.set_password('portfolio-monitor', 'SNAPTRADE_CLIENT_ID', 'YOUR_ID')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'SNAPTRADE_CLIENT_SECRET', 'YOUR_SECRET')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'SNAPTRADE_USER_ID', 'YOUR_USER_ID')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'SNAPTRADE_USER_SECRET', 'YOUR_USER_SECRET')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'gmail_address', 'you@gmail.com')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'gmail_app_password', 'xxxx-xxxx-xxxx-xxxx')"
```

### 4. Configure tiers
```bash
cp tiers.example.yaml tiers.yaml   # then populate with your own tickers
```

### 5. Schedule (macOS launchd)

Create `~/Library/LaunchAgents/com.portfoliomonitor.daily.plist`:

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

- **`tiers.yaml`** — tier assignments, watchlist, exit pool, per-ticker overrides *(kept local — copy from `tiers.example.yaml`)*
- **`config.yaml`** — rule thresholds, on/off switches, scheduler timing

Key config knobs:

```yaml
top_up_compounder:
  tier_1_off_high_threshold: 0.93  # Alert when Tier 1 is 7%+ off 52w high
  off_high_threshold: 0.85         # Tier 2: 15%+ off high
  rsi_gate: 55.0                   # Suppress if RSI > this and not near BB lower

ma_crossover:
  enabled: true
  apply_to_tiers: [tier_1, tier_2]
  cooldown_days: 30
```

A `realized_pnl.yaml` ledger tracks closed positions (also kept local). Copy from `realized_pnl.example.yaml` to start your own.

## Ghostfolio (optional dashboard)

A `docker-compose.yml` is included to run [Ghostfolio](https://ghostfolio.dev) locally — portfolio dashboard, lot store, charts.

```bash
cp .env.example .env          # fill in secrets (openssl rand -hex 32 for each)
docker compose up -d          # starts Ghostfolio + Postgres + Redis at localhost:3333
```

`.env` is kept local. Never commit it. `.env.example` is the template.

### Brokerage CSV → Ghostfolio import

```bash
python -m portfolio_monitor.scripts.broker_to_ghostfolio \
  input.csv output.csv \
  --account-map "<YOUR_ACCOUNT_1>=ghostfolio-account-id-1" \
  --account-map "<YOUR_ACCOUNT_2>=ghostfolio-account-id-2"
```

## Project layout

```
portfolio-monitor/
├── tiers.yaml                  # Your tier assignments (local — copy from tiers.example.yaml)
├── tiers.example.yaml          # Template — populate with your own tickers
├── realized_pnl.yaml           # Closed position ledger (local — copy from realized_pnl.example.yaml)
├── config.yaml                 # Thresholds + runtime config (edit freely)
├── docker-compose.yml          # Optional Ghostfolio stack
├── .env.example                # Ghostfolio secrets template
├── portfolio_monitor/
│   ├── main.py                 # Daily orchestration loop
│   ├── tiers_loader.py         # YAML → AppConfig, TierMap, MaCrossoverConfig, TopUpConfig...
│   ├── portfolio_types.py      # Position, Lot, Portfolio dataclasses
│   ├── snaptrade_client.py     # Live holdings via SnapTrade OAuth
│   ├── market_data.py          # yfinance: prices, ATH, MA50/200, Bollinger Bands, RSI, news
│   ├── store.py                # SQLite cooldown + alert dedup log
│   ├── email_dispatch.py       # Jinja2 HTML → Gmail SMTP
│   ├── rules/
│   │   ├── base.py             # Alert, EvaluationContext, Rule, Severity
│   │   ├── ath_proximity.py    # Rule 0a
│   │   ├── exit_watchlist.py   # Rule 0b
│   │   ├── sell_into_strength.py  # Rule 1
│   │   ├── capitulation.py     # Rule 2
│   │   ├── tier_weakness.py    # Rules 3 + 4
│   │   ├── buy_the_dip.py      # Rules 5 + 6 (with quality tiers)
│   │   ├── concentration.py    # Rule 7
│   │   ├── earnings.py         # Rule 8
│   │   └── ma_crossover.py     # Rule 9 — golden/death cross
│   ├── templates/
│   │   ├── alert.html.j2       # Per-alert email (BB bar, tier badge, lot breakdown)
│   │   └── digest.html.j2      # Weekly Saturday digest
│   └── scripts/
│       ├── run_guarded.py      # Time-gate + sentinel → main.run_once()
│       └── broker_to_ghostfolio.py
└── tests/                      # All offline — no network required
```

## Tests
```bash
pytest tests/   # all offline
```

---

*This is a notification tool, not a financial advisor. Review tier assignments quarterly. Nothing here is investment, tax, or legal advice.*
