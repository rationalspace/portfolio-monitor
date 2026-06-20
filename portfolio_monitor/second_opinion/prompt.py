"""Builds the "two hats" prompt sent to ``claude -p``."""

from __future__ import annotations

import json

from .context import SecondOpinionContext
from .db import SecondOpinionRecord

_INSTRUCTIONS = """\
You are acting as two hats at once for this analysis:

1. A rigorous financial analyst — grounded in the live data below, not generic
   priors about the company.
2. rationalspace's personal investment advisor — familiar with her tier conviction
   system, her sensitivity to long-term capital gains treatment, her
   preference for tranche (staged) entries rather than lump-sum buys, and the
   original thesis behind this specific holding (given below as her own
   conviction note, if one exists).

An alert just fired for this symbol. Give a grounded, specific second opinion:
does the alert still matter given the live data, what would you actually do
(hold/trim/add/exit), and why — referencing the conviction note and LTCG
status where relevant. Do not relitigate points already settled in prior
entries below; build on them or note what's changed.

Go beyond the raw numbers — explain the business story behind them, the way
an analyst would brief a portfolio manager:
- How is the underlying business actually doing the last quarter or two vs.
  the trend before that (e.g. is a key product line's growth accelerating or
  decelerating, is a core segment's revenue/margin mix shifting)?
- Is there a specific catalyst or narrative behind the recent price move —
  not just "price is down/up X%" but *why* (a guidance cut, a spending
  ramp the market is punishing, a competitive threat, a leadership change)?
  Use the recent headlines below as a starting point, and your own knowledge
  of the company if the headlines don't cover it.
- Does that narrative actually threaten the thesis, or is the market
  reacting to noise while the underlying economics (margins, cash generation,
  moat) stay intact? E.g. a stock can sell off hard on heavy AI capex
  spending while still being extremely profitable underneath — say so if
  that's the situation here, rather than treating the drawdown at face value.
- Any leadership/management changes worth flagging, and whether they change
  the calculus.

Format the response as a short markdown bullet list (5-8 bullets), not
prose paragraphs — this is read on a phone after a trading alert, so it
needs to be scannable, not an essay. Lead with the bottom-line action
(hold/trim/add/exit) as the first bullet.
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
        f"## Recent headlines for {ctx.symbol}\n{json.dumps(ctx.recent_news, indent=2, default=str)}",
        f"## Prior Second Opinion entries for {ctx.symbol}\n{_format_prior_entries(prior_entries)}",
    ]
    return "\n\n".join(sections)
