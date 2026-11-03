"""Pull the numbers out of clinical text, whether written as digits or words.

Used by the deterministic verifier: a number in the note must have been said.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_ONES = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SPECIAL = {
    "once": "1", "twice": "2", "single": "1", "double": "2", "triple": "3", "half": "0.5", "dozen": "12",
    "couple": "2", "both": "2", "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "quarter": "0.25",
}
_DIGITS = re.compile(r"(?<![A-Za-z0-9.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![A-Za-z]*\d)")
_WORD = re.compile(r"[a-z]+")


def norm(value: str | float | int) -> str | None:
    try:
        d = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None
    if d == d.to_integral():
        return str(d.quantize(Decimal(1)))
    return format(d.normalize(), "f")


def _digit_numbers(text: str) -> set[str]:
    out = set()
    for m in _DIGITS.finditer(text):
        n = norm(m.group(1) + (m.group(2) or ""))
        if n is not None:
            out.add(n)
    return out


def _word_numbers(text: str, include_special: bool = True) -> set[str]:
    words = _WORD.findall(text.lower().replace("-", " "))
    out: set[str] = set()
    cur: int | None = None
    decimals: str | None = None
    prev_single_digit = False

    def flush():
        nonlocal cur, decimals, prev_single_digit
        if cur is not None:
            out.add(norm(f"{cur}.{decimals}" if decimals else cur))
        cur, decimals, prev_single_digit = None, None, False

    for w in words:
        if w in _SPECIAL:
            flush()
            if include_special:
                out.add(_SPECIAL[w])
            continue
        if decimals is not None:
            if w in _ONES and _ONES[w] < 10:
                decimals += str(_ONES[w])
                continue
            flush()
        if w in _ONES:
            val = _ONES[w]
            if cur is None:
                cur = val
                prev_single_digit = val < 10
            elif cur % 10 == 0 and cur % 100 >= 20 and val < 10:  # "twenty five"
                cur += val
                prev_single_digit = False
            elif cur >= 100 and cur % 100 == 0 and val < 100:  # "two hundred four"
                cur += val
                prev_single_digit = False
            else:
                flush()
                cur = val
                prev_single_digit = val < 10
        elif w in _TENS:
            val = _TENS[w]
            if cur is None:
                cur = val
            elif prev_single_digit and cur < 10:  # "one thirty six" spoken blood pressure
                cur = cur * 100 + val
                prev_single_digit = False
            elif cur >= 100 and cur % 100 == 0:
                cur += val
            else:
                flush()
                cur = val
        elif w == "hundred":
            cur = (cur or 1) * 100
            prev_single_digit = False
        elif w == "thousand":
            cur = (cur or 1) * 1000
            prev_single_digit = False
        elif w == "point" and cur is not None:
            decimals = ""
        elif w in ("and",) and cur is not None and cur >= 100:
            continue
        elif w in ("a", "an") and cur is None:
            continue
        else:
            flush()
    flush()
    return {n for n in out if n is not None}


def numbers_in(text: str, include_special: bool = True) -> set[str]:
    """Numbers in text. include_special=False ignores words like "twice" or "first",
    which notes use for frequencies the transcript may express differently."""
    if not text:
        return set()
    return _digit_numbers(text) | _word_numbers(text, include_special)
