"""Rule 1 at the text boundary: no number a user or an agent reads may be written by an LLM.

Model-written prose (a plan title, a reason, a trade-off) is shown only when it states no quantity. Text that does is
withheld whole, never edited: a sentence with its number cut out can read as a different claim. The caller replaces
withheld text with text built by code from verified results.

The check errs toward withholding. A false alarm costs a sentence of model prose; a miss puts an invented number in
front of an operator.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection

_UNITS = "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_STEMS = "thir|four|fif|six|seven|eigh|nine"
_CARDINAL = f"(?:{_UNITS}|(?:{_STEMS})teen|(?:twen|for|{_STEMS})t(?:y|ies)|hundred|thousand|million|billion|trillion|dozen)"
_ORDINAL = (f"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|(?:{_STEMS})teenth|"
            f"(?:twen|for|{_STEMS})tieth|hundredth|thousandth|millionth|billionth)")
# "score" counts only as twenty ("scores of homes"), never as in "risk score"
_QUANTITY = re.compile(
    rf"\b(?:{_CARDINAL}s?|{_ORDINAL}s?|{_CARDINAL}-?fold|hal(?:f|ve|ves|ved|ving|fway)|quarters?|single|couple|twice|thrice|"
    r"doubl(?:e|es|ed|ing|y)|tripl(?:e|es|ed|ing)|quadrupl(?:e|es|ed|ing)|scores\s+of|(?:a|half\s+a)\s+score)\b")
# Phrases that contain a number word or a digit but state no quantity: building and road types, line construction, the
# N-1 security criterion, idioms and local place names.
ALLOWED = ("single-family", "single-circuit", "double-circuit", "one-way", "two-way", "two-lane", "four-lane", "no one",
           "one another", "first responder", "first aid", "at first", "n-1", "thousand oaks", "three points", "four corners")
_ALLOWED = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in ALLOWED) + r")s?\b")
_ID = re.compile(r"\b[A-Za-z]+\d+\b")


def carries_quantity(text: str, names: Collection[str] = ()) -> bool:
    """True when `text` states a quantity: a digit or numeral in any script (fractions, superscripts and Roman numeral
    signs included) or a number word. `names` are identifiers that hold digits but are names, not quantities (this
    grid's line and bus ids); an id the grid does not have is treated as a number."""
    text = _ID.sub(lambda m: " " if m.group(0) in names else m.group(0), text)
    # format characters (zero-width space, soft hyphen) can split a number word without changing how it reads
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    if any(unicodedata.category(c) in ("Nl", "No") for c in text):  # numeral signs that NFKC would turn into letters
        return True
    folded = _ALLOWED.sub(" ", unicodedata.normalize("NFKC", text).casefold())
    return any(unicodedata.category(c)[0] == "N" for c in folded) or bool(_QUANTITY.search(folded))


def guard(text, names: Collection[str] = (), limit: int | None = None) -> tuple[str, bool]:
    """Model-written text -> (text, False) when it states no quantity, ("", True) when it does and is withheld.
    Anything that is not a string is read as its string form, so a bare number is withheld too."""
    text = "" if text is None else str(text).strip()
    if carries_quantity(text, names):
        return "", True
    return text[:limit], False
