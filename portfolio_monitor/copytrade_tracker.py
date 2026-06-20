"""Copy-trade tracker for a subscription-gated portfolio page.

Logs into a login-gated page that publishes a portfolio manager's buy/sell
activity, scrapes the recent buy/sell section, and returns only rows that
are *new* since the last run (change-detected via a local SQLite table).

The source URL — and any other identifying detail of the subscription
service — lives in ``copytrade_source.yaml`` (local-only, gitignored) rather
than in this file, so the service/person being tracked isn't visible in the
public repo. Copy ``copytrade_source.example.yaml`` to get started.

Credentials live in the macOS Keychain under the ``portfolio-monitor``
service — never in code or config files.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "copytrade_trades.db"
_SOURCE_CONFIG_PATH = Path(__file__).resolve().parent.parent / "copytrade_source.yaml"


def _portfolio_url() -> str:
    """Read the tracked portfolio page URL from the local-only source config."""
    if not _SOURCE_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"{_SOURCE_CONFIG_PATH} not found. Copy copytrade_source.example.yaml "
            "to copytrade_source.yaml and fill in the portfolio_url."
        )
    data = yaml.safe_load(_SOURCE_CONFIG_PATH.read_text()) or {}
    url = data.get("portfolio_url")
    if not url:
        raise ValueError(f"{_SOURCE_CONFIG_PATH} is missing a 'portfolio_url' key.")
    return url


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class CopyTradeEntry:
    symbol: str
    direction: str          # "buy" or "sell"
    trade_date: str         # ISO date string as reported on the page
    price: float | None
    quantity: float | None
    notes: str              # raw text from the row


# ── Persistent state (SQLite) ─────────────────────────────────────────────────

def _get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            trade_date  TEXT NOT NULL,
            price       REAL,
            quantity    REAL,
            notes       TEXT,
            seen_at     TEXT NOT NULL,
            UNIQUE(symbol, direction, trade_date, price, quantity)
        )
    """)
    conn.commit()
    return conn


def _filter_new(trades: list[CopyTradeEntry], db_path: Path = DB_PATH) -> list[CopyTradeEntry]:
    """Return only trades not yet seen; persist the new ones.

    Dedup key is (symbol, direction, trade_date, price, quantity) — the
    semantically stable fields. ``notes`` is excluded because it embeds the
    page's "Total Allocation changed to X%" text, which drifts day to day
    for the same real-world trade and would otherwise defeat dedup (checked
    via a manual SELECT rather than relying solely on the UNIQUE constraint,
    since older on-disk DBs may predate the corrected constraint above).
    """
    conn = _get_db(db_path)
    new: list[CopyTradeEntry] = []
    now = datetime.utcnow().isoformat()
    for t in trades:
        existing = conn.execute(
            "SELECT 1 FROM seen_trades WHERE symbol = ? AND direction = ? AND trade_date = ? "
            "AND price IS ? AND quantity IS ?",
            (t.symbol, t.direction, t.trade_date, t.price, t.quantity),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO seen_trades (symbol, direction, trade_date, price, quantity, notes, seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (t.symbol, t.direction, t.trade_date, t.price, t.quantity, t.notes, now),
        )
        new.append(t)
    conn.commit()
    conn.close()
    return new


# ── Scraper ───────────────────────────────────────────────────────────────────

def _parse_price(text: str) -> float | None:
    text = text.replace("$", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _parse_qty(text: str) -> float | None:
    text = text.replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _normalise_symbol(raw: str) -> str:
    """Upper-case, strip punctuation, keep only the ticker part."""
    return re.sub(r"[^A-Z0-9]", "", raw.upper()).strip()


def _parse_date(raw: str) -> str:
    """Try common date formats; fall back to raw string."""
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return raw.strip()


def scrape_trades(*, headless: bool = True, dump_html: bool = False) -> list[CopyTradeEntry]:
    """Log in and return all visible buy/sell trades from the portfolio page.

    Set ``dump_html=True`` during first-run debugging to print the raw page HTML
    so selectors can be verified / tuned.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

    from .secrets import SecretKey, get_secret

    portfolio_url = _portfolio_url()
    email = get_secret(SecretKey.COPYTRADE_EMAIL)
    password = get_secret(SecretKey.COPYTRADE_PASSWORD)

    trades: list[CopyTradeEntry] = []

    with sync_playwright() as pw:
        from playwright_stealth import Stealth
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        Stealth().apply_stealth_sync(page)  # Mask headless fingerprint (bypasses anti-bot checks)

        # ── Step 1: navigate to portfolio page (login form is embedded there) ──
        log.info("Loading portfolio page (login form embedded)…")
        page.goto(portfolio_url, wait_until="domcontentloaded", timeout=30_000)
        log.info("Loaded: %s", page.url)

        if dump_html:
            log.info("=== PRE-LOGIN PAGE HTML (first 3000 chars) ===\n%s", page.content()[:3000])

        # Check if login form is present (WordPress Elementor widget: name="log" / "pwd")
        if page.locator('input[name="log"]').count() > 0:
            log.info("Login form detected — filling credentials…")
            page.fill('input[name="log"]', email)
            page.fill('input[name="pwd"]', password)
            with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                page.click('button[name="wp-submit"]')

            if dump_html:
                log.info("=== POST-LOGIN PAGE (url=%s, first 2000 chars) ===\n%s",
                         page.url, page.content()[:2000])

            # After login, WordPress may redirect to /wp-admin or /my-account.
            # Navigate directly to the portfolio page and verify the form is gone there.
            if page.url != portfolio_url and page.url.rstrip("/") != portfolio_url.rstrip("/"):
                log.info("Redirected to %s — navigating back to portfolio page", page.url)
                page.goto(portfolio_url, wait_until="domcontentloaded", timeout=30_000)

            # Now check if the login form is gone on the PORTFOLIO page specifically
            if page.locator('input[name="log"]').count() > 0:
                log.error("Login form still present on portfolio page — bad credentials or bot block")
                browser.close()
                return []
            log.info("Login succeeded — portfolio page is unlocked")
        else:
            log.info("No login form on portfolio page — already authenticated or page is public")

        if dump_html:
            html = page.content()
            log.info("=== PORTFOLIO PAGE HTML (first 8000 chars) ===\n%s", html[:8000])

        # ── Step 3: wait for dynamic trade content to load ──────────────────
        try:
            page.wait_for_function(
                "document.body.innerText.includes('Bought') || document.body.innerText.includes('Sold')",
                timeout=20_000,
            )
        except PwTimeout:
            log.warning("Timed out waiting for trade content — page may be slow or empty")

        trades = _extract_trades(page)
        log.info("Extracted %d trade rows from page", len(trades))

        if not trades and dump_html:
            log.info("=== FULL PAGE HTML ===\n%s", page.content())

        browser.close()

    return trades


def _extract_trades(page) -> list[CopyTradeEntry]:
    """Extract trades from the page's ``.notifications__box`` elements.

    Each trade card has:
      <div class="notifications__box notifications__box--buy|sell show">
        <img ...>
        SYMBOL
        DD/MM/YYYY
        Bought/Sold N shares of Company (SYM) at an average price of $X per share. ...
      </div>

    Falls back to free-text scan if the DOM structure changes.
    """
    trades: list[CopyTradeEntry] = []

    # Primary: precise selector for the page's trade cards
    # Note: only the newest entry has class "show"; select all boxes regardless
    boxes = page.locator(".notifications__box").all()
    if boxes:
        log.info("Found %d .notifications__box elements", len(boxes))
        for box in boxes:
            t = _parse_notification_box(box)
            if t:
                trades.append(t)
        return trades

    # Fallback: broad free-text scan
    log.warning("No .notifications__box found — falling back to text scan")
    full_text = page.locator("body").inner_text()
    idx = full_text.find("Recent Buy & Sell")
    section = full_text[idx:] if idx >= 0 else full_text
    # Cut at the next heading to avoid scanning the rest of the page
    for next_heading in ("Stock Holdings", "Hedging Strategy", "Current Holdings"):
        cut = section.find(next_heading, 20)
        if cut > 0:
            section = section[:cut]
            break
    trades = _parse_free_text_block(section)
    return trades


_NOTIFICATION_RE = re.compile(
    r"(?P<dir>Bought|Sold)\s+(?P<qty>[\d,.]+)\s+shares\s+of\s+.+?\s+at\s+an\s+average\s+price\s+of\s+\$(?P<price>[\d,.]+)",
    re.IGNORECASE,
)


def _parse_notification_box(box) -> CopyTradeEntry | None:
    """Parse a single .notifications__box element into a CopyTradeEntry."""
    try:
        text = box.inner_text().strip()
    except Exception:
        return None

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 3:
        return None

    # Line 0: ticker symbol (e.g. "NVDA")
    # Line 1: date in DD/MM/YYYY
    # Line 2+: description sentence
    symbol = _normalise_symbol(lines[0])
    raw_date = lines[1]
    description = " ".join(lines[2:])

    if not symbol or len(symbol) < 2:
        return None

    trade_date = _parse_date(raw_date)

    m = _NOTIFICATION_RE.search(description)
    if not m:
        # Try to infer direction from class
        classes = box.get_attribute("class") or ""
        if "buy" in classes.lower():
            direction = "buy"
        elif "sell" in classes.lower():
            direction = "sell"
        else:
            return None
        price = None
        qty = None
    else:
        direction = "buy" if m.group("dir").lower() == "bought" else "sell"
        price = _parse_price(m.group("price"))
        qty = _parse_qty(m.group("qty"))

    return CopyTradeEntry(
        symbol=symbol,
        direction=direction,
        trade_date=trade_date,
        price=price,
        quantity=qty,
        notes=description[:200],
    )


def _parse_table_row(headers: list[str], cells: list[str]) -> CopyTradeEntry | None:
    """Map table cells to CopyTradeEntry using header names."""
    row: dict[str, str] = {}
    for i, h in enumerate(headers):
        if i < len(cells):
            row[h] = cells[i]

    # Detect direction
    direction = None
    for key in ("action", "type", "direction", "buy/sell"):
        if key in row:
            v = row[key].lower()
            if "buy" in v:
                direction = "buy"
            elif "sell" in v:
                direction = "sell"
            break
    # Sometimes the column header itself is "buy" or "sell"
    if direction is None:
        for h in headers:
            if h in ("buy", "bought"):
                direction = "buy"
                break
            if h in ("sell", "sold"):
                direction = "sell"
                break

    if direction is None:
        return None

    # Find symbol
    symbol = None
    for key in ("ticker", "symbol", "stock", "name", "company"):
        if key in row and row[key]:
            symbol = _normalise_symbol(row[key])
            break
    if not symbol:
        return None

    # Find date
    trade_date = "unknown"
    for key in ("date", "trade date", "transaction date", "when"):
        if key in row and row[key]:
            trade_date = _parse_date(row[key])
            break

    price = _parse_price(row.get("price", "") or row.get("price ($)", "") or "")
    qty = _parse_qty(row.get("qty", "") or row.get("quantity", "") or row.get("shares", "") or "")
    notes = " | ".join(f"{k}={v}" for k, v in row.items() if v)

    return CopyTradeEntry(
        symbol=symbol,
        direction=direction,
        trade_date=trade_date,
        price=price,
        quantity=qty,
        notes=notes,
    )


# Regex to match a line like:  BUY  AAPL  $185.00  10 shares  Jun 10 2025
# or:  Bought 10 shares of MSFT at $420 on June 1
_TRADE_LINE_RE = re.compile(
    r"(?P<dir>buy|bought|sell|sold)\s+"
    r"(?:(?P<qty1>\d[\d,.]*)\s+(?:shares?\s+of\s+)?)?(?P<sym>[A-Z]{2,5})"
    r"(?:\s+(?P<qty2>\d[\d,.]*)\s+shares?)?"
    r"(?:\s+(?:at|@)\s+\$?(?P<price>[\d,.]+))?"
    r"(?:\s+on\s+(?P<date>[A-Za-z0-9 ,/\-]+))?",
    re.IGNORECASE,
)

_SYMBOL_DATE_RE = re.compile(
    r"(?P<sym>[A-Z]{2,5})\s*[:\-–]\s*(?P<dir>buy|bought|sell|sold)",
    re.IGNORECASE,
)


def _parse_free_text_block(text: str) -> list[CopyTradeEntry]:
    """Extract trades from unstructured text using regex patterns."""
    trades: list[CopyTradeEntry] = []
    seen = set()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = _TRADE_LINE_RE.search(line)
        if not m:
            m = _SYMBOL_DATE_RE.search(line)
        if not m:
            continue

        d = m.groupdict()
        raw_dir = (d.get("dir") or "").lower()
        direction = "buy" if raw_dir in ("buy", "bought") else "sell" if raw_dir in ("sell", "sold") else None
        if not direction:
            continue

        sym = _normalise_symbol(d.get("sym") or "")
        if not sym or len(sym) < 2:
            continue

        # Skip common English words accidentally matched as tickers
        SKIP = {"THE", "AND", "FOR", "WITH", "AT", "ON", "BY", "TO", "OF", "IN", "IS", "OR", "IT"}
        if sym in SKIP:
            continue

        qty_raw = d.get("qty1") or d.get("qty2") or ""
        price_raw = d.get("price") or ""
        date_raw = d.get("date") or date.today().isoformat()

        key = (sym, direction, date_raw[:10])
        if key in seen:
            continue
        seen.add(key)

        trades.append(CopyTradeEntry(
            symbol=sym,
            direction=direction,
            trade_date=_parse_date(date_raw),
            price=_parse_price(price_raw),
            quantity=_parse_qty(qty_raw),
            notes=line[:200],
        ))

    return trades


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_new_trades(*, headless: bool = True, dump_html: bool = False,
                     db_path: Path = DB_PATH) -> list[CopyTradeEntry]:
    """Scrape the tracked portfolio page and return only trades seen for the first time.

    This is the function called by the rule engine. Returns [] if login fails,
    no trades found, or all trades were already seen in a previous run.
    """
    all_trades = scrape_trades(headless=headless, dump_html=dump_html)
    if not all_trades:
        return []
    new = _filter_new(all_trades, db_path=db_path)
    log.info("New trades (not seen before): %d / %d", len(new), len(all_trades))
    return new
