"""Luhn checksum validator for credit card numbers.

Implements the Luhn algorithm (ISO/IEC 7812-1) used to validate that a
number sequence is a potentially valid credit card number. This runs as
a post-match filter after regex detection to reduce false positives.
"""

from __future__ import annotations

import re


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def is_valid_luhn(text: str) -> bool:
    """Return True if *text* passes the Luhn checksum.

    Non-digit characters (spaces, hyphens) are stripped before validation.
    Strings shorter than 13 digits or longer than 19 digits are rejected
    per ISO/IEC 7812 length bounds.
    """
    digits = _digits_only(text)
    if len(digits) < 13 or len(digits) > 19:
        return False

    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0
