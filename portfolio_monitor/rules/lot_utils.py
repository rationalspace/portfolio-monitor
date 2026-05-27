"""Shared lot-analysis helper used by any rule that needs per-lot tax detail.

Both sell_into_strength and exit_watchlist fire for exit-pool tickers and need
the same lot breakdown — this module keeps that logic in one place.
"""

from __future__ import annotations

from datetime import date, timedelta

_LTCG_DAYS = 365


def lot_analysis(positions: list, price: float, lt_rate: float, st_rate: float) -> list[dict]:
    """Return a per-lot breakdown for all positions in a symbol.

    Each row contains:
      acquired_on      — ISO date string
      days_held        — calendar days since acquisition
      quantity         — shares in this lot
      cost_per_share   — cost basis per share
      gain_per_share   — unrealised gain/loss per share at current price
      gain_pct         — gain_per_share / cost_per_share
      gain_total       — total unrealised gain/loss for the lot
      is_long_term     — True if held > 365 days (LTCG eligible)
      is_profitable    — True if gain_per_share > 0
      lt_eligible_on   — human-readable date the lot first qualifies as LT
      days_to_lt       — days until LT eligibility (negative = already LT)
      tax_rate         — effective rate applied (lt_rate or st_rate)
      est_tax          — estimated tax on the gain (0 for loss lots)
      after_tax_gain   — gain_total minus est_tax

    Sorted: LT profitable first → LT underwater → ST.
    """
    cutoff = date.today() - timedelta(days=_LTCG_DAYS)
    today = date.today()
    rows = []
    for pos in positions:
        for lot in pos.lots:
            days_held = (today - lot.acquired_on).days
            is_lt = lot.acquired_on <= cutoff
            cost = lot.cost_basis_per_share
            gain_per_share = price - cost
            gain_pct = gain_per_share / cost if cost else 0.0
            gain_total = gain_per_share * lot.quantity
            lt_eligible_on = lot.acquired_on + timedelta(days=_LTCG_DAYS)
            days_to_lt = (lt_eligible_on - today).days  # negative = already LT
            rate = lt_rate if is_lt else st_rate
            est_tax = max(0.0, gain_total) * rate
            after_tax_gain = gain_total - est_tax
            rows.append({
                "acquired_on": lot.acquired_on.isoformat(),
                "days_held": days_held,
                "quantity": lot.quantity,
                "cost_per_share": cost,
                "gain_per_share": gain_per_share,
                "gain_pct": gain_pct,
                "gain_total": gain_total,
                "is_long_term": is_lt,
                "is_profitable": gain_per_share > 0,
                "lt_eligible_on": lt_eligible_on.strftime("%b %-d, %Y"),
                "days_to_lt": days_to_lt,
                "tax_rate": rate,
                "est_tax": est_tax,
                "after_tax_gain": after_tax_gain,
            })
    rows.sort(key=lambda r: (not r["is_long_term"], not r["is_profitable"], r["acquired_on"]))
    return rows


def lot_tax_summary(lots: list[dict], lt_rate: float, st_rate: float) -> dict:
    """Aggregate tax scenario totals from a lot_analysis result.

    Returns a dict ready to merge into an alert payload:
      lt_profitable_qty, lt_profitable_gain, has_lt_profitable_lots
      all_short_term, earliest_lt_date, days_to_earliest_lt
      lt_rate, st_rate
      lt_gross, lt_tax, lt_after_tax
      has_st_profitable, st_gross, st_tax, st_after_tax
    """
    lt_profitable = [l for l in lots if l["is_long_term"] and l["is_profitable"]]
    st_lots = [l for l in lots if not l["is_long_term"]]
    st_profitable = [l for l in st_lots if l["is_profitable"]]
    all_short_term = len(st_lots) == len(lots) and len(lots) > 0

    earliest_lt_date = None
    days_to_earliest_lt = None
    if all_short_term and st_lots:
        earliest = min(st_lots, key=lambda l: l["days_to_lt"])
        earliest_lt_date = earliest["lt_eligible_on"]
        days_to_earliest_lt = earliest["days_to_lt"]

    lt_gross = sum(l["gain_total"] for l in lt_profitable)
    lt_tax = sum(l["est_tax"] for l in lt_profitable)

    st_gross = sum(l["gain_total"] for l in st_profitable)
    st_tax = sum(l["est_tax"] for l in st_profitable)

    return {
        "lt_profitable_qty": sum(l["quantity"] for l in lt_profitable),
        "lt_profitable_gain": lt_gross - lt_tax,  # after-tax for the green banner
        "has_lt_profitable_lots": bool(lt_profitable),
        "all_short_term": all_short_term,
        "earliest_lt_date": earliest_lt_date,
        "days_to_earliest_lt": days_to_earliest_lt,
        "lt_rate": lt_rate,
        "st_rate": st_rate,
        "lt_gross": lt_gross,
        "lt_tax": lt_tax,
        "lt_after_tax": lt_gross - lt_tax,
        "has_st_profitable": bool(st_profitable),
        "st_gross": st_gross,
        "st_tax": st_tax,
        "st_after_tax": st_gross - st_tax,
    }
