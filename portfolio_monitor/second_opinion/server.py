"""Local-only FastAPI server for "Second Opinion" alert follow-ups.

Binds to localhost only — run via uvicorn, e.g.:

    uvicorn portfolio_monitor.second_opinion.server:app --host 127.0.0.1 --port 8765

Single-user-Mac assumption: binding to localhost keeps this unreachable from
outside the machine, but any local process/user account can still hit it.
See CLAUDE.md for the full threat-model note. Do not bind 0.0.0.0.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..humanize import humanize_rule_name
from .claude_runner import ClaudeRunError, run_claude
from .context import gather_context
from .db import SecondOpinionStore
from .prompt import build_prompt

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Portfolio Monitor — Second Opinion")
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["humanize_rule"] = humanize_rule_name
_store = SecondOpinionStore()


@app.get("/second-opinion", response_class=HTMLResponse)
def second_opinion(
    symbol: str = Query(...),
    rule: str = Query(...),
    date: str | None = Query(None),
    dry_run: bool = Query(False),
) -> str:
    """Returns instantly with a spinner; the page's own JS calls /second-opinion/run
    in the background (the part that actually takes 10-15s) and redirects once done."""
    template = _env.get_template("loading.html.j2")
    return template.render(symbol=symbol.upper(), rule=rule, date=date, dry_run=dry_run)


@app.get("/second-opinion/run", response_class=JSONResponse)
def second_opinion_run(
    symbol: str = Query(...),
    rule: str = Query(...),
    date: str | None = Query(None),
    dry_run: bool = Query(False),
) -> dict:
    symbol = symbol.upper()
    date = date or None
    ctx = gather_context(symbol, rule, date)
    prior = _store.recent_for_symbol(symbol, limit=2)
    prompt = build_prompt(ctx, prior)

    if dry_run:
        response_markdown = (
            "_Dry run — `claude -p` was not invoked. "
            f"Prompt was {len(prompt)} chars; context fields populated: "
            f"snapshot={ctx.snapshot is not None}, fundamentals={ctx.fundamentals is not None}, "
            f"conviction_note={ctx.conviction_note is not None}._\n\n"
            f"```\n{prompt}\n```"
        )
    else:
        try:
            response_markdown = run_claude(prompt)
        except ClaudeRunError as exc:
            log.error("Second Opinion failed for %s/%s: %s", symbol, rule, exc)
            response_markdown = f"_Second Opinion failed: {exc}_"

    record = _store.record(
        symbol=symbol,
        rule=rule,
        alert_date=date,
        prompt=prompt,
        response_markdown=response_markdown,
    )
    return {"id": record.id}


@app.get("/opinion/{record_id}", response_class=HTMLResponse)
def opinion_detail(record_id: int) -> str:
    record = _store.get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No such Second Opinion entry")
    template = _env.get_template("result.html.j2")
    return template.render(record=record)


@app.get("/history", response_class=HTMLResponse)
def history() -> str:
    records = _store.all_history()
    template = _env.get_template("history.html.j2")
    return template.render(records=records, symbol=None)


@app.get("/history/{symbol}", response_class=HTMLResponse)
def history_for_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    records = _store.history_for_symbol(symbol)
    template = _env.get_template("history.html.j2")
    return template.render(records=records, symbol=symbol)
