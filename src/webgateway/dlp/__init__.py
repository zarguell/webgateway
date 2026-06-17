"""DLP (Data Loss Prevention) middleware package."""

from __future__ import annotations

from webgateway.dlp.middleware import DlpBlockedError, DlpMiddleware, DlpOutcome
from webgateway.dlp.scanner import DlpMatch, DlpScanner

__all__ = [
    "DlpBlockedError",
    "DlpMatch",
    "DlpMiddleware",
    "DlpOutcome",
    "DlpScanner",
]
