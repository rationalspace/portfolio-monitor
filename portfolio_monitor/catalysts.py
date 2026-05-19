"""Lightweight news-headline tagger.

Phase 1 detects catalysts from headline text alone (yfinance ``news`` field).
The classifier is intentionally simple: keyword matches on title/summary, with
a small whitelist of high-signal phrases. False positives are tolerable because
the user reads the headline before acting.

Phase 2 swap-in for Benzinga/AlphaVantage will replace ``classify`` only — the
return type is stable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# Tag → list of regex patterns (lowercase comparison).
PATTERNS: dict[str, list[str]] = {
    "ceo_change": [
        r"\b(new|appoints?|names?|elects?)\s+(ceo|chief executive|president)",
        r"\bceo\b.*\b(steps? down|resigns?|retires?|out)",
        r"\bnew chief executive\b",
    ],
    "ma_rumor": [
        r"\b(takeover|acqui[rs]i?[a-z]*|merg[a-z]+|buyout)\b",
        r"\bin talks (?:to|with)\b",
        r"\b(deal|offer) (?:to|for)\b.*\b(acqui|buy|takeover)\b",
    ],
    "activist": [
        r"\bactivist\b",
        r"\b13d\b",
        r"\b(elliott|starboard|trian|engine no\. 1|pershing square)\b",
    ],
    "earnings_beat": [
        r"\b(beats?|tops?|exceeds?) (estimate|expectation|consensus|street)",
        r"\bearnings beat\b",
    ],
    "guidance_cut": [
        r"\b(cuts?|lowers?|slashes?) (?:full[-\s]?year|annual|fy)? ?(?:guidance|outlook|forecast)",
        r"\bguidance (?:cut|reduced|lowered)\b",
        r"\b(misses?|disappoints?)\b.*\b(guidance|outlook)\b",
    ],
    "regulatory": [
        r"\b(sec|ftc|doj) (?:probe|investigation|charges|lawsuit)",
        r"\b(antitrust|regulatory) (?:concerns?|action|probe)",
    ],
    "analyst_upgrade": [
        r"\bupgraded?\b.*\b(analyst|to\s+(?:buy|outperform|overweight))",
        r"\b(price target|pt) (?:raised|increased)\b",
    ],
    "analyst_downgrade": [
        r"\bdowngraded?\b",
        r"\b(price target|pt) (?:cut|reduced|lowered)\b",
    ],
}


@dataclass(frozen=True)
class CatalystTag:
    tag: str          # one of the keys in PATTERNS
    title: str        # headline that triggered the tag
    url: str | None
    publisher: str | None


def classify(news_items: Iterable[dict[str, Any]]) -> list[CatalystTag]:
    """Return catalyst tags found in the given news items.

    Multiple tags can be returned per item if more than one pattern matches
    (e.g. an "earnings beat" with an "analyst upgrade" follow-up). Items that
    don't match anything are silently dropped.
    """
    out: list[CatalystTag] = []
    for item in news_items:
        title = (item.get("title") or "").lower()
        if not title:
            continue
        for tag, patterns in PATTERNS.items():
            if any(re.search(p, title) for p in patterns):
                out.append(
                    CatalystTag(
                        tag=tag,
                        title=item.get("title") or "",
                        url=item.get("url"),
                        publisher=item.get("publisher"),
                    )
                )
    return out


def has_recent_guidance_cut(news_items: Iterable[dict[str, Any]]) -> bool:
    """Helper used by the buy-the-dip "no recent guidance cut" filter."""
    return any(t.tag == "guidance_cut" for t in classify(news_items))
