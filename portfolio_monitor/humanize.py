"""Turn a snake_case rule name into a readable label, preserving acronyms.

``rule.replace('_', ' ')|title`` (the naive Jinja approach) turns ``ath_proximity``
into "Ath Proximity" — wrong, since ATH/LTCG/etc. are acronyms, not words.
"""

from __future__ import annotations

_ACRONYMS = {"ath", "ltcg", "ma", "rsi", "pe", "eps", "fcf", "yoy", "bb", "sma", "ema", "coe"}


def humanize_rule_name(rule: str) -> str:
    words = rule.replace("_", " ").split()
    return " ".join(w.upper() if w.lower() in _ACRONYMS else w.capitalize() for w in words)
