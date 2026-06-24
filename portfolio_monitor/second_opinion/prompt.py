"""Builds the "two hats" prompt sent to ``claude -p``."""

from __future__ import annotations

import json

from .context import SecondOpinionContext
from .db import SecondOpinionRecord

_INSTRUCTIONS = """\
Two hats: (1) a rigorous financial analyst grounded in the live data below,
not generic priors; (2) rationalspace's personal investment advisor, who knows her
tier conviction system, her LTCG sensitivity, her preference for tranche
entries over lump-sum buys, and this holding's thesis (her conviction note
below, if any).

An alert just fired. Decide whether it still matters given the live data,
what to actually do (hold/trim/add/exit), and why — referencing the
conviction note and LTCG status where relevant. Don't relitigate points
already settled in prior entries below; build on them or flag what changed.

Go beyond the numbers — explain the business story behind them: is the
underlying business accelerating or decelerating vs. recent quarters; what
specific catalyst (not just "price moved X%") is behind the move — a
guidance cut, a capex ramp the market is punishing, a competitive threat, a
leadership change (use the headlines below, plus your own knowledge if they
don't cover it); does that catalyst actually threaten the thesis or is the
market reacting to noise while margins/cash generation/moat stay intact
(e.g. heavy AI capex can tank a stock that's still extremely profitable
underneath — say so if that's the case here).

Output: a markdown bullet list, 5-8 bullets, action-first (lead with
hold/trim/add/exit). No prose paragraphs — this is read on a phone.
"""


def _format_prior_entries(prior: list[SecondOpinionRecord]) -> str:
    if not prior:
        return "(No prior Second Opinion entries for this symbol.)"
    lines = []
    for rec in prior:
        lines.append(
            f"- {rec.requested_at.date().isoformat()} ({rec.rule}): "
            f"{_truncate(rec.response_markdown, 400)}"
        )
    return "\n".join(lines)


def _truncate(text: str, n: int) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[:n].rstrip() + "..."


def build_prompt(ctx: SecondOpinionContext, prior_entries: list[SecondOpinionRecord]) -> str:
    sections = [
        _INSTRUCTIONS,
        f"## Alert\nSymbol: {ctx.symbol}\nRule fired: {ctx.rule}\n"
        f"Alert date: {ctx.alert_date or 'today'}\nTier: {ctx.tier}",
        f"## rationalspace's conviction note for {ctx.symbol}\n"
        f"{ctx.conviction_note or '(No conviction note on file for this symbol.)'}",
        f"## LTCG status\n{ctx.ltcg_status}",
        f"## Live price/technical snapshot\n{json.dumps(ctx.snapshot, indent=2)}",
        f"## Fundamentals snapshot\n{json.dumps(ctx.fundamentals, indent=2)}",
        f"## Recent alert history for {ctx.symbol} (most recent first)\n"
        f"{json.dumps(ctx.recent_alerts, indent=2)}",
        f"## Recent tracked copy-trade activity in {ctx.symbol}\n"
        f"{json.dumps(ctx.recent_copytrade_activity, indent=2, default=str)}",
        f"## Recent headlines for {ctx.symbol}\n{json.dumps(ctx.recent_news, indent=2)}",
        f"## Prior Second Opinion entries for {ctx.symbol}\n{_format_prior_entries(prior_entries)}",
    ]
    return "\n\n".join(sections)
