"""Provider resource manager — unified circuit breaker + quota tracking.

Tracks per-provider failure budgets (circuit breaker) and usage budgets
(quotas) in a single SQLite database. Persisted across restarts.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from serp_llm.config import GatewayConfig

if TYPE_CHECKING:
    from serp_llm.injection.events import EventLogger

__all__ = ["ProviderResourceManager"]

logger = logging.getLogger(__name__)

_USAGE_RETENTION_DAYS = 90


class ProviderResourceManager:
    """Unified resource governance: circuit breaker + quota tracking.

    Args:
        db_path: Path to the SQLite database file.
        config: Full gateway config (reads circuit_breaker, quotas sections).
        event_logger: Optional EventLogger for emitting state-transition events.
    """

    def __init__(
        self,
        db_path: str,
        config: GatewayConfig,
        event_logger: EventLogger | None = None,
    ) -> None:
        self._config = config
        self._db_path = db_path
        self._event_logger = event_logger
        self._alerted_providers: set[str] = set()
        self._conn: sqlite3.Connection | None = None
        self._init_db()
        self._prune_old_usage()

    # ── DB lifecycle ────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS circuit_breaker_state (
                provider        TEXT PRIMARY KEY,
                state           TEXT NOT NULL DEFAULT 'closed',
                failure_count   INTEGER NOT NULL DEFAULT 0,
                window_start_ts REAL,
                last_failure_ts REAL,
                opened_at_ts    REAL,
                cooldown_seconds INTEGER NOT NULL DEFAULT 120
            );

            CREATE TABLE IF NOT EXISTS provider_usage (
                id          INTEGER PRIMARY KEY,
                ts          REAL NOT NULL,
                provider    TEXT NOT NULL,
                operation   TEXT NOT NULL,
                request_id  TEXT,
                success     BOOLEAN NOT NULL,
                latency_ms  INTEGER,
                error_class TEXT,
                cost_units  REAL NOT NULL DEFAULT 0.0
            );

            CREATE INDEX IF NOT EXISTS idx_usage_provider_ts
                ON provider_usage(provider, ts);
        """)
        self._conn.commit()

    def _prune_old_usage(self) -> None:
        """Delete usage rows older than retention period."""
        cutoff = time.time() - _USAGE_RETENTION_DAYS * 86400
        self._conn.execute("DELETE FROM provider_usage WHERE ts < ?", (cutoff,))
        self._conn.commit()

    def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Circuit breaker ────────────────────────────────────────────

    def _get_cb_config(self, provider: str):
        """Return the circuit breaker config for *provider*, falling back to default."""
        providers = self._config.circuit_breaker.providers
        return providers.get(provider) or providers.get("default")

    def _load_cb_state(self, provider: str) -> dict[str, Any]:
        """Load circuit breaker state from DB, or return defaults."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT state, failure_count, window_start_ts, opened_at_ts, cooldown_seconds "
            "FROM circuit_breaker_state WHERE provider = ?",
            (provider,),
        ).fetchone()
        if row is None:
            return {
                "state": "closed",
                "failure_count": 0,
                "window_start_ts": None,
                "opened_at_ts": None,
                "cooldown_seconds": 120,
            }
        return {
            "state": row[0],
            "failure_count": row[1],
            "window_start_ts": row[2],
            "opened_at_ts": row[3],
            "cooldown_seconds": row[4],
        }

    def _save_cb_state(self, provider: str, state: dict[str, Any]) -> None:
        """Upsert circuit breaker state into DB."""
        assert self._conn is not None
        self._conn.execute(
            """INSERT OR REPLACE INTO circuit_breaker_state
               (provider, state, failure_count, window_start_ts, last_failure_ts,
                opened_at_ts, cooldown_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                provider,
                state["state"],
                state["failure_count"],
                state.get("window_start_ts"),
                state.get("last_failure_ts"),
                state.get("opened_at_ts"),
                state.get("cooldown_seconds", 120),
            ),
        )
        self._conn.commit()

    async def record_failure(self, provider: str, error_class: str | None = None) -> None:
        """Record a provider failure. May trip the circuit if threshold exceeded."""
        if not self._config.circuit_breaker.enabled:
            return
        cb_cfg = self._get_cb_config(provider)
        if cb_cfg is None:
            return
        now = time.time()
        state = self._load_cb_state(provider)

        if state["state"] == "half_open":
            state["state"] = "open"
            state["opened_at_ts"] = now
            state["failure_count"] = 1
            state["window_start_ts"] = now
            state["last_failure_ts"] = now
            state["cooldown_seconds"] = cb_cfg.cooldown_seconds
            self._save_cb_state(provider, state)
            logger.warning(
                "Circuit OPEN for %s (half-open probe failed, error=%s)", provider, error_class
            )
            if self._event_logger:
                self._event_logger.log_event(
                    event="circuit_open",
                    provider=provider,
                    error_class=error_class,
                    cooldown_seconds=cb_cfg.cooldown_seconds,
                )
            return

        if state["state"] == "open":
            state["last_failure_ts"] = now
            self._save_cb_state(provider, state)
            return

        # Closed: sliding window logic
        ws = state["window_start_ts"]
        if ws is None or (now - ws) > cb_cfg.window_seconds:
            state["window_start_ts"] = now
            state["failure_count"] = 1
        else:
            state["failure_count"] += 1

        state["last_failure_ts"] = now

        if state["failure_count"] >= cb_cfg.error_threshold:
            state["state"] = "open"
            state["opened_at_ts"] = now
            state["cooldown_seconds"] = cb_cfg.cooldown_seconds
            logger.warning(
                "Circuit OPEN for %s (threshold=%d, error=%s)",
                provider,
                cb_cfg.error_threshold,
                error_class,
            )
            if self._event_logger:
                self._event_logger.log_event(
                    event="circuit_open",
                    provider=provider,
                    error_class=error_class,
                    cooldown_seconds=cb_cfg.cooldown_seconds,
                )

        self._save_cb_state(provider, state)

    async def record_success(self, provider: str) -> None:
        """Record a provider success. Closes or half-closes the circuit."""
        if not self._config.circuit_breaker.enabled:
            return
        state = self._load_cb_state(provider)
        now = time.time()

        if state["state"] == "half_open":
            state["state"] = "closed"
            state["failure_count"] = 0
            state["window_start_ts"] = None
            state["opened_at_ts"] = None
            self._save_cb_state(provider, state)
            logger.info("Circuit CLOSED for %s (half-open probe succeeded)", provider)
            if self._event_logger:
                self._event_logger.log_event(
                    event="circuit_closed",
                    provider=provider,
                )
            return

        if state["state"] == "open":
            opened = state.get("opened_at_ts")
            cooldown = state.get("cooldown_seconds", 120)
            if opened is not None and (now - opened) >= cooldown:
                state["state"] = "half_open"
                self._save_cb_state(provider, state)
                logger.info("Circuit HALF-OPEN for %s (cooldown expired)", provider)
            return

        # Closed: reset failure count
        state["failure_count"] = 0
        state["window_start_ts"] = None
        self._save_cb_state(provider, state)

    async def get_circuit_state(self, provider: str) -> str:
        """Return current circuit state: 'closed', 'open', or 'half_open'."""
        if not self._config.circuit_breaker.enabled:
            return "closed"
        state = self._load_cb_state(provider)
        if state["state"] == "open":
            opened = state.get("opened_at_ts")
            cooldown = state.get("cooldown_seconds", 120)
            if opened is not None and (time.time() - opened) >= cooldown:
                state["state"] = "half_open"
                self._save_cb_state(provider, state)
                return "half_open"
        return state["state"]

    async def reset_circuit(self, provider: str) -> None:
        """Admin: force-close a circuit breaker."""
        assert self._conn is not None
        self._conn.execute("DELETE FROM circuit_breaker_state WHERE provider = ?", (provider,))
        self._conn.commit()
        logger.info("Circuit RESET (admin) for %s", provider)

    # ── Quota tracking ─────────────────────────────────────────────

    async def record_usage(
        self,
        provider: str,
        operation: str,
        request_id: str,
        success: bool,
        latency_ms: int,
        error_class: str | None = None,
        cost_units: float = 0.0,
    ) -> None:
        """Log one usage row to the provider_usage table."""
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO provider_usage (ts, provider, operation, request_id, success, "
            "latency_ms, error_class, cost_units) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), provider, operation, request_id, success, latency_ms,
             error_class, cost_units),
        )
        self._conn.commit()

    async def get_quota_info(self, provider: str) -> dict[str, Any]:
        """Return quota info: calls_month, limit_month, calls_today, limit_today.

        Also includes pct_used and exhausted flag.
        """
        assert self._conn is not None
        now = time.time()
        dt_now = datetime.fromtimestamp(now, tz=UTC)
        start_of_month = datetime(dt_now.year, dt_now.month, 1, tzinfo=UTC).timestamp()
        start_of_today = datetime(
            dt_now.year, dt_now.month, dt_now.day, tzinfo=UTC
        ).timestamp()

        qc = self._conn.execute(
            "SELECT COALESCE(SUM(cost_units), 0) FROM provider_usage "
            "WHERE provider = ? AND ts >= ?",
            (provider, start_of_month),
        ).fetchone()[0]
        qd = self._conn.execute(
            "SELECT COALESCE(SUM(cost_units), 0) FROM provider_usage "
            "WHERE provider = ? AND ts >= ?",
            (provider, start_of_today),
        ).fetchone()[0]

        qcfg = self._config.quotas.providers.get(provider)

        limit_month = qcfg.monthly_limit if qcfg else None
        limit_today = qcfg.daily_limit if qcfg else None

        if limit_month is not None and limit_month > 0:
            pct = (qc / limit_month) * 100.0
        elif limit_today is not None and limit_today > 0:
            pct = (qd / limit_today) * 100.0
        else:
            pct = 0.0

        if (
            qcfg
            and pct >= qcfg.alert_at_percent
            and provider not in self._alerted_providers
        ):
            self._alerted_providers.add(provider)
            if self._event_logger:
                self._event_logger.log_event(
                    event="quota_alert",
                    provider=provider,
                    pct_used=round(pct, 1),
                    limit_month=limit_month,
                )

        exhausted = False
        if limit_month is not None and qc >= limit_month:
            exhausted = True
        if limit_today is not None and qd >= limit_today:
            exhausted = True

        if exhausted and self._event_logger:
            self._event_logger.log_event(
                event="quota_exhausted",
                provider=provider,
                calls_month=qc,
                limit_month=limit_month,
            )

        return {
            "calls_month": qc,
            "limit_month": limit_month,
            "calls_today": qd,
            "limit_today": limit_today,
            "pct_used": round(pct, 1),
            "exhausted": exhausted,
        }

    async def override_quota(self, provider: str, remaining: int) -> None:
        """Admin: adjust remaining quota by inserting a negative usage entry."""
        qcfg = self._config.quotas.providers.get(provider)
        if qcfg is None or qcfg.monthly_limit is None:
            return
        assert self._conn is not None
        current = await self.get_quota_info(provider)
        used = current["calls_month"]
        target_used = max(0, qcfg.monthly_limit - remaining)
        delta = target_used - used
        self._conn.execute(
            "INSERT INTO provider_usage (ts, provider, operation, request_id, success, cost_units) "
            "VALUES (?, ?, 'adjustment', 'admin', 1, ?)",
            (time.time(), provider, delta),
        )
        self._conn.commit()

    async def reset_quota(self, provider: str) -> None:
        """Admin: reset all usage for the current month for a provider."""
        assert self._conn is not None
        now = time.time()
        dt_now = datetime.fromtimestamp(now, tz=UTC)
        start_of_month = datetime(dt_now.year, dt_now.month, 1, tzinfo=UTC).timestamp()
        self._conn.execute(
            "DELETE FROM provider_usage WHERE provider = ? AND ts >= ?",
            (provider, start_of_month),
        )
        self._conn.commit()

    # ── Routing integration ────────────────────────────────────────

    async def filter_available(self, candidates: list[str]) -> list[str]:
        """Filter out OPEN providers, reorder by quota consumption (ascending).

        Removes providers whose circuit is OPEN or whose quota is exhausted
        with action=remove_from_pool. Sorts remaining by quota percentage
        (lowest first). Exhausted fallback_only providers are appended at end.

        Returns:
            Filtered and reordered candidate list. Empty list if none available.
        """
        if not self._config.circuit_breaker.enabled and not self._config.quotas.providers:
            return candidates

        available: list[tuple[str, float]] = []
        fallback_only: list[tuple[str, float]] = []

        for name in candidates:
            cb_state = await self.get_circuit_state(name)
            if cb_state == "open":
                continue

            qcfg = self._config.quotas.providers.get(name)
            if qcfg is not None:
                info = await self.get_quota_info(name)
                if info["exhausted"]:
                    if qcfg.exhausted_action == "remove_from_pool":
                        continue
                    else:
                        fallback_only.append((name, info["pct_used"]))
                        continue
                available.append((name, info["pct_used"]))
            else:
                available.append((name, 0.0))

        available.sort(key=lambda x: x[1])
        fallback_only.sort(key=lambda x: x[1])
        return [name for name, _ in available] + [name for name, _ in fallback_only]

    # ── Health / summary ──────────────────────────────────────────

    async def get_summary(self) -> dict[str, dict[str, Any]]:
        """Per-provider summary across all configured providers."""
        result: dict[str, dict[str, Any]] = {}
        providers = set(self._config.providers.keys())
        for name in sorted(providers):
            cb_state = await self.get_circuit_state(name)
            qi = await self.get_quota_info(name)
            result[name] = {
                "circuit_state": cb_state,
                "calls_today": qi["calls_today"],
                "calls_month": qi["calls_month"],
                "limit_month": qi["limit_month"],
                "quota_pct": qi["pct_used"],
            }
        return result

    async def get_history(self, provider: str, days: int = 30) -> list[dict[str, Any]]:
        """Daily rollup with p50/p95 latency percentiles for the given provider."""
        assert self._conn is not None
        now = time.time()
        cutoff = now - days * 86400

        agg_rows = self._conn.execute(
            """SELECT
                   CAST(ts / 86400 AS INTEGER) * 86400 AS day_bucket,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS errors
               FROM provider_usage
               WHERE provider = ? AND ts >= ?
               GROUP BY day_bucket
               ORDER BY day_bucket ASC""",
            (provider, cutoff),
        ).fetchall()

        if not agg_rows:
            return []

        day_buckets = [row[0] for row in agg_rows]

        latency_rows = self._conn.execute(
            """SELECT
                   CAST(ts / 86400 AS INTEGER) * 86400 AS day_bucket,
                   latency_ms
               FROM provider_usage
               WHERE provider = ? AND ts >= ? AND latency_ms IS NOT NULL""",
            (provider, cutoff),
        ).fetchall()

        latencies_by_day: dict[int, list[int]] = {d: [] for d in day_buckets}
        for day_ts, lat_ms in latency_rows:
            if day_ts in latencies_by_day:
                latencies_by_day[day_ts].append(lat_ms)

        result = []
        for row in agg_rows:
            day_ts = row[0]
            calls = row[1]
            errors = row[2] or 0
            day_lats = sorted(latencies_by_day.get(day_ts, []))
            p50 = day_lats[len(day_lats) // 2] if day_lats else 0
            p95_idx = int(len(day_lats) * 0.95) if day_lats else -1
            p95 = (
                day_lats[p95_idx]
                if day_lats and p95_idx < len(day_lats)
                else (day_lats[-1] if day_lats else 0)
            )
            result.append({
                "date": datetime.fromtimestamp(day_ts, tz=UTC).strftime("%Y-%m-%d"),
                "calls": calls,
                "errors": errors,
                "error_rate": round(errors / calls * 100, 1) if calls > 0 else 0.0,
                "latency_p50_ms": p50,
                "latency_p95_ms": p95,
            })
        return result
