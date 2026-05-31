"""Daily orchestrator.

Pipeline:

1. Load tiers.yaml + config.yaml
2. Pull portfolio from SnapTrade
3. For each rule (in order), evaluate against the portfolio + market data
4. Apply per-(symbol, rule) cooldown gate
5. Dispatch HIGH/MEDIUM alerts immediately, accumulate DIGEST items
6. Persist to the audit log

Run via:
- ``python -m portfolio_monitor.main``           — full daily run
- ``python -m portfolio_monitor.scripts.run_once --dry-run`` — no email send
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from .compliance_checker import get_compliance_status
from .email_dispatch import EmailDispatcher
from .market_data import MarketData
from .rules import ALL_RULES, Alert, EvaluationContext, Severity
from .snaptrade_client import SnapTradeClient
from .store import Store
from .tiers_loader import AppConfig, TierMap, load_config, load_tiers, project_root

log = logging.getLogger(__name__)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # yfinance logs expected 404s (ETFs/crypto have no fundamentals on Yahoo) at
    # ERROR level internally. Real failures still surface as exceptions we catch.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def run_once(*, dry_run: bool = False, send_digest: bool = False) -> int:
    """Single end-to-end pass. Returns count of alerts emitted."""
    root = project_root()
    config = load_config(root / "config.yaml")
    tiers = load_tiers(root / "tiers.yaml")

    configure_logging(config.logging.level)
    log.info("Starting portfolio monitor run (dry_run=%s, send_digest=%s)", dry_run, send_digest)

    portfolio = _fetch_portfolio()
    market = MarketData(ath_history_period=config.data_sources.ath_history_period)
    store = Store()
    dispatcher = EmailDispatcher(config.alerts.email)

    ctx = EvaluationContext(
        portfolio=portfolio,
        tiers=tiers,
        config=config,
        market=market,
    )

    compliance = get_compliance_status(root / "coe_trade_log.yaml")
    digest_pile: list[Alert] = []
    sent_count = 0

    for rule_cls in ALL_RULES:
        rule = rule_cls(config)
        if not rule.enabled:
            log.info("Rule %s disabled — skipping.", rule.name)
            continue
        try:
            alerts = rule.evaluate(ctx)
        except Exception:  # noqa: BLE001
            log.exception("Rule %s crashed — continuing with remaining rules", rule.name)
            continue

        for alert in alerts:
            # Cooldown priority: per-ticker override → rule-specific → global default.
            # exit_watchlist uses a 1-day cooldown so rallies keep alerting daily.
            per_ticker = tiers.override_for(alert.symbol).alert_cooldown_days
            rule_specific = getattr(config, alert.rule, None)
            rule_specific_days = getattr(rule_specific, "cooldown_days", None) if rule_specific else None
            cooldown_days = per_ticker or rule_specific_days or config.alerts.cooldown_days
            if store.in_cooldown(alert.symbol, alert.rule, cooldown_days):
                log.debug("Cooldown skip: %s/%s", alert.symbol, alert.rule)
                continue

            # Attach compliance status to every alert payload before dispatch.
            alert.payload["compliance"] = compliance

            store.record(
                symbol=alert.symbol,
                rule=alert.rule,
                severity=alert.severity.value,
                title=alert.title,
                payload=alert.payload,
            )

            if alert.severity == Severity.DIGEST:
                digest_pile.append(alert)
                continue

            log.info("Dispatch: %s [%s]", alert.title, alert.severity.value)
            try:
                dispatcher.send_alert(alert, dry_run=dry_run)
                sent_count += 1
            except Exception:  # noqa: BLE001
                log.exception("Failed to send alert for %s — continuing", alert.symbol)

    if send_digest and digest_pile:
        log.info("Sending weekly digest with %d items", len(digest_pile))
        dispatcher.send_digest(digest_pile, dry_run=dry_run)
        sent_count += 1
    elif digest_pile:
        log.info("Holding %d digest items for the next weekly send", len(digest_pile))

    store.prune(retain_days=config.logging.retain_days)
    log.info("Run complete — %d alert(s) dispatched", sent_count)
    return sent_count


def _fetch_portfolio():
    """Pull the latest portfolio from SnapTrade.

    Wrapped so tests can monkeypatch in a fixture portfolio without going through
    SnapTrade's network calls.
    """
    client = SnapTradeClient()
    return client.fetch_portfolio()


def cli() -> None:
    parser = argparse.ArgumentParser(prog="portfolio-monitor")
    parser.add_argument("--dry-run", action="store_true", help="Skip email send; log instead.")
    parser.add_argument("--send-digest", action="store_true", help="Force-send weekly digest now.")
    args = parser.parse_args()

    try:
        run_once(dry_run=args.dry_run, send_digest=args.send_digest)
    except Exception:  # noqa: BLE001
        log.exception("Run failed")
        sys.exit(1)


if __name__ == "__main__":
    cli()
