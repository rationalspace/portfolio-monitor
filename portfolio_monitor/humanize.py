"""Turn a snake_case rule name into a readable label, preserving acronyms.

``rule.replace('_', ' ')|title`` (the naive Jinja approach) turns ``ath_proximity``
into "Ath Proximity" — wrong, since ATH/LTCG/etc. are acronyms, not words.
"""

from __future__ import annotations

_ACRONYMS = {"ath", "ltcg", "ma", "rsi", "pe", "eps", "fcf", "yoy", "bb", "sma", "ema", "coe"}


def humanize_rule_name(rule: str) -> str:
    words = rule.replace("_", " ").split()
    return " ".join(w.upper() if w.lower() in _ACRONYMS else w.capitalize() for w in words)


def signed_dollar(value: float, decimals: int = 0) -> str:
    """Format a signed dollar amount with the sign before the currency symbol.

    Python's ``{:+,.0f}`` format puts the sign on the digits, so a literal
    ``${{ "{:+,.0f}".format(x) }}`` in a template renders "$+1,234" for a
    gain instead of the conventional "+$1,234".
    """
    sign = "-" if value < 0 else "+"
    return f"{sign}${abs(value):,.{decimals}f}"


def signed_rupee(value: float, decimals: int = 0) -> str:
    sign = "-" if value < 0 else "+"
    return f"{sign}₹{abs(value):,.{decimals}f}"
