"""Structured JSON audit logger — one append-only line per request.

Implements PRD section 4.7. Uses Python's ``logging`` module with a
``RotatingFileHandler`` so that file rotation and thread-safety come for free.
The output is JSON Lines (``.jsonl``), chosen for trivial SIEM ingestion.

Each log entry is a complete ``AuditEntry`` serialised to a single JSON object.
Timestamps are always UTC ISO-8601.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from serp_llm.config import LoggingConfig

__all__ = ["AuditEntry", "AuditLogger"]

_LOG_FORMAT = "%(message)s"
_LOGGER_NAME = "serp_llm.audit"


# ---------------------------------------------------------------------------
# Audit entry
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """A single audit record (PRD §4.7).

    The ``type`` field uses ``"extract"`` (not ``"scrape"``) to match the
    API-facing naming convention.
    """

    request_id: str
    api_key_id: str
    type: Literal["search", "extract"]
    url: str
    provider_used: str
    latency_ms: int
    status: Literal["success", "error", "blocked"]
    attempt_number: int = 1
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    policy_matched: str | None = None
    proxy_used: str | None = None
    judge_invoked: bool = False
    judge_reasoning_tag: str | None = None
    dlp_policy: str | None = None
    dlp_action: Literal["pass", "block", "redact", "reroute", "log"] | None = None
    dlp_match_count: int = 0
    cache_hit: bool = False
    quality_check_passed: bool = True
    cache_invalidated: bool = False
    session_profile: str | None = None
    session_valid: bool | None = None
    session_expired: bool | None = None
    fingerprint_id: str | None = None
    browser_service: str | None = None
    browser_engine: str | None = None
    firefox_version: str | None = None
    extractor_used: str | None = None
    extraction_fallback: bool = False
    content_length_raw: int = 0
    content_length_processed: int = 0
    content_unchanged: bool = False
    # Prompt injection detection (PRD §27.10)
    injection_checked: bool = False
    injection_detected: bool = False
    injection_type: str | None = None
    injection_action: str | None = None
    injection_heuristic_score: float = 0.0
    injection_classifier_score: float = 0.0
    injection_layer_triggered: str | None = None


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------


class AuditLogger:
    """Append-only rotating JSON audit logger.

    The directory containing ``config.path`` is created if missing. The
    underlying ``logging.Logger`` is a named singleton, so multiple
    ``AuditLogger`` instances (e.g. after a config reload) reconfigure the same
    logger — old handlers are closed and replaced.
    """

    def __init__(self, config: LoggingConfig) -> None:
        self._config = config

        log_path = Path(config.path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        # Replace any handlers from a previous configuration so a new path /
        # rotation size takes effect immediately.
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

        file_handler = RotatingFileHandler(
            config.path,
            maxBytes=config.max_bytes,
            backupCount=config.backup_count,
        )
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(file_handler)

        self._logger = logger

    async def log(self, entry: AuditEntry) -> None:
        """Serialise *entry* to a single JSON line and write it."""
        record = asdict(entry)
        self._logger.info(json.dumps(record, default=str))
