from __future__ import annotations

import re

_ZERO_WIDTH_CHARS = [
    '\u200b',  # zero-width space
    '\u200c',  # zero-width non-joiner
    '\u200d',  # zero-width joiner
    '\ufeff',  # BOM / zero-width no-break space
    '\u00ad',  # soft hyphen
    '\u2060',  # word joiner
]

_ZERO_WIDTH_TABLE = str.maketrans('', '', ''.join(_ZERO_WIDTH_CHARS))

_DEFAULT_BOILERPLATE_PATTERNS: list[str] = [
    r"(?i)^(cookie policy|accept cookies|privacy policy)\s*$",
    r"(?i)^(subscribe to (our )?newsletter)\s*$",
    r"(?i)^(share this article|share on)\s*.*$",
]


def clean_markdown(
    md: str,
    extra_patterns: list[str] | None = None,
) -> str:
    """Normalize markdown: collapse whitespace, remove boilerplate lines."""
    # Strip zero-width and Unicode obfuscation chars (PRD §27.6)
    md = md.translate(_ZERO_WIDTH_TABLE)
    md = re.sub(r"\n{3,}", "\n\n", md)
    lines = md.splitlines()
    md = "\n".join(line for line in lines if line.strip() or line == "")
    patterns = list(_DEFAULT_BOILERPLATE_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)
    for pattern in patterns:
        md = re.sub(pattern, "", md, flags=re.MULTILINE)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()
