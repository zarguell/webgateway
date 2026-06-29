"""events.jsonl writer for prompt injection detections (PRD §27.10).

Appends structured JSON events to a file. Each event is a single JSON object
on its own line.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from serp_llm.alerting import AlertDispatcher

logger = logging.getLogger(__name__)


class EventLogger:
    """Append-only JSON Lines event writer.

    The file is created on first write. Each event is a single JSON object
    on its own line.
    """

    def __init__(
        self,
        events_path: str = "",
        *,
        alert_dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self._path = events_path
        self._alert_dispatcher = alert_dispatcher

    def log_event(self, **fields: Any) -> None:
        """Write a single event to the JSONL file.

        Automatically adds a ``ts`` timestamp. If ``events_path`` is empty,
        the event is logged to Python's logging instead (no-op for file).
        """
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            **fields,
        }

        if not self._path:
            logger.info("injection_event: %s", json.dumps(entry))
        else:
            try:
                path = Path(self._path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
            except Exception as exc:
                logger.warning("Failed to write injection event: %s", exc)

        if self._alert_dispatcher is not None:
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(
                    self._alert_dispatcher.dispatch(entry)
                )
