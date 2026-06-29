"""Admin UI — Jinja2 + HTMX pages for operators.

All routes under ``/admin/*`` (except login) are protected by httpOnly
session cookies. The login endpoint accepts an admin API key and issues
a signed session cookie (24h TTL).

Templates live in ``src/serp_llm/templates/``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from serp_llm.admin_session import AdminSessionManager
from serp_llm.cache.store import CacheStore
from serp_llm.key_store import KeyStore
from serp_llm.providers.registry import ProviderRegistry
from serp_llm.resource_manager import ProviderResourceManager
from serp_llm.sessions.store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin_ui"])

# Jinja2 template loader — resolves from the templates directory
_templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)


# ---------------------------------------------------------------------------
# Jinja2 custom filters
# ---------------------------------------------------------------------------

def _timestamp_to_str(ts: float | None) -> str:
    """Convert a Unix timestamp to a human-readable date string."""
    if ts is None:
        return "Never"
    try:
        dt = datetime.fromtimestamp(ts, tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, ValueError, OverflowError):
        return str(ts)


_templates.env.filters["timestamp_to_str"] = _timestamp_to_str
_templates.env.filters["tojson"] = json.dumps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session_manager(request: Request) -> AdminSessionManager:
    mgr: AdminSessionManager | None = getattr(
        request.app.state, "admin_session_manager", None
    )
    if mgr is None:
        raise HTTPException(status_code=503, detail="Session manager not available")
    return mgr


def _require_admin_session(
    request: Request,
    session_cookie: str | None = None,
) -> AdminSessionManager | None:
    """Check the admin session cookie and return the manager if valid."""
    mgr = _get_session_manager(request)
    cookie = session_cookie or request.cookies.get(mgr.cookie_name)
    session = mgr.verify_session(cookie)
    if session is None or not session.is_admin:
        return None
    return mgr


def _get_common_context(request: Request) -> dict:
    """Return common template variables."""
    mgr = _get_session_manager(request)
    session_cookie = request.cookies.get(mgr.cookie_name, "")
    csrf_token = mgr.generate_csrf_token(session_cookie) if session_cookie else ""
    return {
        "request": request,
        "session": getattr(request.state, "admin_session", None),
        "csrf_token": csrf_token,
    }


def _verify_csrf(
    mgr: AdminSessionManager, session_cookie: str | None, csrf_token: str
) -> None:
    """Validate CSRF token. Raises HTTPException(403) on failure."""
    if not session_cookie:
        raise HTTPException(status_code=403, detail="No admin session")
    if not mgr.verify_csrf_token(session_cookie, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or expired CSRF token")


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------


@router.get("/admin/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    error: str = "",
):
    """Render the admin login form."""
    ctx = _get_common_context(request)
    ctx["error"] = error
    return _templates.TemplateResponse(request, "login.html", ctx)


@router.post("/admin/login")
async def login_submit(
    request: Request,
    api_key: str = Form(...),
    csrf_token: str = Form(default=""),
):
    """Validate an admin API key and issue a session cookie.

    Accepts: config-based keys with ``admin: true``, SQLite-backed keys
    with ``role: admin``, and the bootstrap key (when applicable).
    """
    # CSRF check
    mgr_csrf = _get_session_manager(request)
    session_cookie = request.cookies.get(mgr_csrf.cookie_name, "")
    _verify_csrf(mgr_csrf, session_cookie, csrf_token)

    from serp_llm.auth import _find_key

    key = _find_key(api_key, request)
    if key is None or not key.admin:
        return _templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "session": None,
                "error": "Invalid API key or not an admin key",
            },
            status_code=401,
        )

    mgr = _get_session_manager(request)
    cookie_value = mgr.create_session(key_id=key.id, role="admin")

    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie(
        key=mgr.cookie_name,
        value=cookie_value,
        max_age=mgr.cookie_max_age,
        path=mgr.cookie_path,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/admin/logout")
async def logout(request: Request):
    """Clear the admin session cookie and redirect to login."""
    mgr = _get_session_manager(request)
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(
        key=mgr.cookie_name,
        path=mgr.cookie_path,
    )
    return response


# ---------------------------------------------------------------------------
# Session auth dependency for UI pages
# ---------------------------------------------------------------------------


def _ui_session(
    request: Request,
    admin_session: str | None = Cookie(default=None),
) -> None:
    """Verify admin session cookie for UI page access.

    Sets ``request.state.admin_session`` on success.

    Raises:
        HTTPException(302): Redirects to login if session is invalid.
    """
    mgr = _get_session_manager(request)
    session = mgr.verify_session(admin_session)
    if session is None or not session.is_admin:
        raise HTTPException(status_code=303, detail="Redirecting to login")
    request.state.admin_session = session


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    _unused: None = Depends(_ui_session),
):
    """Admin dashboard — provider health, request volume, cache hit rate, alerts."""
    ctx = _get_common_context(request)

    # Provider health
    registry: ProviderRegistry | None = getattr(
        request.app.state, "provider_registry", None
    )
    rm: ProviderResourceManager | None = getattr(
        request.app.state, "resource_manager", None
    )

    providers = []
    if registry is not None:
        health_map = {}
        try:
            health_map = await registry.health_check_all()
        except Exception:
            logger.exception("Health check failed")

        meta_list = registry.list_metadata()
        meta_by_name = {m.name: m for m in meta_list}

        for name in sorted(health_map.keys()):
            healthy = health_map.get(name, False)
            meta = meta_by_name.get(name)
            circuit_state = "closed"
            calls_today = 0
            quota_pct = None
            cost_units_today = 0.0

            if rm is not None:
                try:
                        circuit_state = await rm.get_circuit_state(name)
                        qi = await rm.get_quota_info(name)
                        calls_today = qi["calls_today"]
                        quota_pct = qi["pct_used"]
                except Exception:
                    pass

            providers.append({
                "name": name,
                "healthy": healthy,
                "circuit_state": circuit_state,
                "calls_today": calls_today,
                "quota_pct": quota_pct,
                "cost_units_today": cost_units_today,
                "last_check_ts": None,
                "quota_exhausted": meta and any(
                    getattr(meta, "warnings", [])
                ),
            })
    ctx["providers"] = providers

    # Sparkline data (from resource manager usage log, last 24h)
    sparkline_data = _build_sparkline(rm)
    ctx["sparkline_data"] = sparkline_data

    # Cache stats (no hit/miss counters available from CacheStore)
    cache_store: CacheStore | None = getattr(
        request.app.state, "cache_store", None
    )
    ctx["cache_hits"] = 0
    ctx["cache_misses"] = 0
    ctx["cache_hit_rate"] = 0.0
    if cache_store is not None:
        try:
            cs = await cache_store.stats()
            ctx["cache_hits"] = cs.get("total_entries", 0)
        except Exception:
            pass

    # Active alerts from events.jsonl
    events_path = os.environ.get("EVENTS_LOG_PATH", "logs/events.jsonl")
    alerts = _read_recent_alerts(events_path, limit=10)
    ctx["alerts"] = alerts

    return _templates.TemplateResponse(request, "dashboard.html", ctx)


# ---------------------------------------------------------------------------
# API Keys page
# ---------------------------------------------------------------------------


@router.get("/admin/keys", response_class=HTMLResponse)
async def keys_page(
    request: Request,
    _unused: None = Depends(_ui_session),
):
    """Admin UI: API key management page."""
    ctx = _get_common_context(request)
    store: KeyStore | None = getattr(request.app.state, "key_store", None)
    keys = store.list_keys() if store else []
    ctx["keys"] = [
        {
            "key_id": k.id,
            "label": k.label,
            "role": k.role,
            "created_ts": k.created_ts,
            "last_used_ts": k.last_used_ts,
            "revoked": k.revoked,
        }
        for k in keys
    ]
    return _templates.TemplateResponse(request, "keys.html", ctx)


# ---------------------------------------------------------------------------
# Providers page
# ---------------------------------------------------------------------------


@router.get("/admin/providers", response_class=HTMLResponse)
async def providers_page(
    request: Request,
    _unused: None = Depends(_ui_session),
):
    """Admin UI: provider status and management page."""
    ctx = _get_common_context(request)
    registry: ProviderRegistry | None = getattr(
        request.app.state, "provider_registry", None
    )
    rm: ProviderResourceManager | None = getattr(
        request.app.state, "resource_manager", None
    )

    providers = []
    if registry is not None:
        import contextlib
        health_map = {}
        with contextlib.suppress(Exception):
            health_map = await registry.health_check_all()

        for meta in registry.list_metadata():
            healthy = health_map.get(meta.name, False)
            circuit_state = "closed"
            quota_pct = None
            cost_units_today = 0.0

            if rm is not None:
                try:
                    circuit_state = await rm.get_circuit_state(meta.name)
                    qi = await rm.get_quota_info(meta.name)
                    quota_pct = qi["pct_used"]
                    cost_units_today = qi["calls_today"]
                except Exception:
                    pass

            providers.append({
                "name": meta.name,
                "healthy": healthy,
                "circuit_state": circuit_state,
                "quota_pct": quota_pct,
                "cost_units_today": cost_units_today,
                "specialization": meta.specialization,
                "warnings": list(meta.warnings) if meta.warnings else [],
            })
    ctx["providers"] = providers
    return _templates.TemplateResponse(request, "providers.html", ctx)


# ---------------------------------------------------------------------------
# Sessions page
# ---------------------------------------------------------------------------


@router.get("/admin/sessions", response_class=HTMLResponse)
async def sessions_page(
    request: Request,
    _unused: None = Depends(_ui_session),
):
    """Admin UI: session management page."""
    ctx = _get_common_context(request)
    store: SessionStore | None = getattr(
        request.app.state, "session_store", None
    )
    sessions = []
    if store is not None:
        try:
            for info in store.list_sessions():
                sessions.append({
                    "session_id": info.session_id,
                    "domain": info.domain,
                    "browser": info.browser_service,
                    "expiry": info.expiry_ts,
                    "last_used_ts": info.last_used_ts,
                    "valid": not (
                        info.expiry_ts is not None
                        and time.time() > info.expiry_ts
                    ),
                    "expired": (
                        info.expiry_ts is not None
                        and time.time() > info.expiry_ts
                    ),
                })
        except Exception:
            logger.exception("Failed to list sessions")
    ctx["sessions"] = sessions
    return _templates.TemplateResponse(request, "sessions.html", ctx)


# ---------------------------------------------------------------------------
# Usage page
# ---------------------------------------------------------------------------


@router.get("/admin/usage", response_class=HTMLResponse)
async def usage_page(
    request: Request,
    range: str = Query(default="24h"),
    _unused: None = Depends(_ui_session),
):
    """Admin UI: usage statistics page."""
    ctx = _get_common_context(request)
    ctx["range"] = range

    rm: ProviderResourceManager | None = getattr(
        request.app.state, "resource_manager", None
    )
    registry: ProviderRegistry | None = getattr(
        request.app.state, "provider_registry", None
    )

    # Per-provider stats
    provider_stats = []
    if rm is not None and registry is not None:
        for name in registry.list_names():
            try:
                history = await rm.get_history(name, days=_range_to_days(range))
                calls = sum(h.get("calls", 0) for h in history)
                errors = sum(h.get("errors", 0) for h in history)
                latencies_p50 = [
                    h.get("latency_p50_ms", 0)
                    for h in history
                    if h.get("latency_p50_ms")
                ]
                latencies_p95 = [
                    h.get("latency_p95_ms", 0)
                    for h in history
                    if h.get("latency_p95_ms")
                ]
                stats = {
                    "provider": name,
                    "calls": calls,
                    "errors": errors,
                    "error_rate": (
                        (errors / calls * 100) if calls > 0 else 0.0
                    ),
                    "p50_latency": (
                        sorted(latencies_p50)[len(latencies_p50) // 2]
                        if latencies_p50 else 0
                    ),
                    "p95_latency": (
                        sorted(latencies_p95)[int(len(latencies_p95) * 0.95)]
                        if latencies_p95 else 0
                    ),
                    "cost_units": 0.0,
                }
                provider_stats.append(stats)
            except Exception:
                pass
    ctx["provider_stats"] = provider_stats

    # Per-key stats (from audit log)
    ctx["key_stats"] = _compute_key_stats(_range_to_days(range))

    return _templates.TemplateResponse(request, "usage.html", ctx)


# ---------------------------------------------------------------------------
# Logs page
# ---------------------------------------------------------------------------


@router.get("/admin/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    api_key_id: str = Query(default=""),
    provider: str = Query(default=""),
    status: str = Query(default=""),
    cache_hit: str = Query(default=""),
    _unused: None = Depends(_ui_session),
):
    """Admin UI: live log viewer with HTMX auto-refresh."""
    ctx = _get_common_context(request)
    filters = {
        "api_key_id": api_key_id,
        "provider": provider,
        "status": status,
        "cache_hit": cache_hit,
    }
    ctx["filters"] = filters

    # Read last 100 entries from gateway.jsonl
    log_path = _get_log_path()
    entries = _read_log_entries(
        log_path, limit=100, filters=filters
    )
    ctx["entries"] = entries

    return _templates.TemplateResponse(request, "logs.html", ctx)


# ---------------------------------------------------------------------------
# Logs partial (for HTMX polling)
# ---------------------------------------------------------------------------


@router.get("/admin/logs/partial", response_class=HTMLResponse)
async def logs_partial(
    request: Request,
    api_key_id: str = Query(default=""),
    provider: str = Query(default=""),
    status: str = Query(default=""),
    cache_hit: str = Query(default=""),
    _unused: None = Depends(_ui_session),
):
    """HTMX partial: latest log entries for auto-refresh."""
    filters = {
        "api_key_id": api_key_id,
        "provider": provider,
        "status": status,
        "cache_hit": cache_hit,
    }
    log_path = _get_log_path()
    entries = _read_log_entries(log_path, limit=100, filters=filters)
    return _templates.TemplateResponse(
        request, "logs_partial.html", {"request": request, "entries": entries}
    )


# ---------------------------------------------------------------------------
# Cache page
# ---------------------------------------------------------------------------


@router.get("/admin/cache", response_class=HTMLResponse)
async def cache_page(
    request: Request,
    _unused: None = Depends(_ui_session),
):
    """Admin UI: cache management page."""
    ctx = _get_common_context(request)
    cache_store: CacheStore | None = getattr(
        request.app.state, "cache_store", None
    )

    stats = {"total_entries": 0, "size_bytes": 0, "expired_entries": 0}
    hit_rate = 0.0
    top_domains = []
    providers = []

    if cache_store is not None:
        try:
            s = await cache_store.stats()
            stats = {
                "total_entries": s.get("total_entries", 0),
                "size_bytes": s.get("size_bytes", 0),
                "expired_entries": s.get("expired_entries", 0),
            }
        except Exception:
            pass

    registry: ProviderRegistry | None = getattr(
        request.app.state, "provider_registry", None
    )
    if registry is not None:
        providers = registry.list_names()

    ctx["stats"] = stats
    ctx["hit_rate"] = hit_rate
    ctx["top_domains"] = top_domains
    ctx["providers"] = providers

    return _templates.TemplateResponse(request, "cache.html", ctx)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_log_path() -> str:
    """Return the gateway JSONL log path from config or default."""
    return os.environ.get("GATEWAY_LOG_PATH", "logs/gateway.jsonl")


def _read_log_entries(
    log_path: str,
    limit: int = 100,
    filters: dict | None = None,
) -> list[dict]:
    """Return the last N entries from a JSONL log file, optionally filtered."""
    path = Path(log_path)
    if not path.exists():
        return []

    filters = filters or {}
    entries = []
    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not _matches_filters(entry, filters):
                    continue
                entries.append(entry)
    except OSError:
        return []

    return entries[-limit:]


def _matches_filters(entry: dict, filters: dict) -> bool:
    """Check if a log entry matches all active filters."""
    for key, filter_val in filters.items():
        if not filter_val:
            continue
        if key == "api_key_id":
            if filter_val not in str(entry.get("api_key_id", "")):
                return False
        elif key == "provider":
            if filter_val not in str(entry.get("provider_used", "")):
                return False
        elif key == "status":
            if entry.get("status") != filter_val:
                return False
        elif key == "cache_hit":
            want = filter_val.lower() == "true"
            if bool(entry.get("cache_hit")) != want:
                return False
    return True


def _read_recent_alerts(events_path: str, limit: int = 10) -> list[dict]:
    """Return the last N unresolved alerts from events.jsonl."""
    path = Path(events_path)
    if not path.exists():
        return []

    import contextlib

    alerts = []
    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    alerts.append(json.loads(stripped))
    except OSError:
        pass
    return alerts[-limit:]


def _build_sparkline(
    rm: ProviderResourceManager | None,
) -> list[dict]:
    """Build 24h sparkline data from provider usage."""
    if rm is None:
        return []
    try:
        buckets: dict[int, int] = {}
        for i in range(24):
            buckets[i] = 0
        return [
            {"hour": f"{h:02d}:00", "count": buckets.get(h, 0)}
            for h in range(24)
        ]
    except Exception:
        return []


def _compute_key_stats(days: int) -> list[dict]:
    """Compute per-key stats from the audit log."""
    log_path = _get_log_path()
    entries = _read_log_entries(log_path, limit=5000)
    key_map: dict[str, dict] = {}
    for entry in entries:
        kid = entry.get("api_key_id", "unknown")
        if kid not in key_map:
            key_map[kid] = {
                "key_id": kid,
                "calls": 0,
                "top_domain": None,
                "top_provider": None,
                "_domains": {},
                "_providers": {},
            }
        key_map[kid]["calls"] += 1
        url = entry.get("url", "")
        if url:
            from urllib.parse import urlparse

            try:
                domain = urlparse(url).netloc
                dmap = key_map[kid]["_domains"]
                dmap[domain] = dmap.get(domain, 0) + 1
            except Exception:
                pass
        prov = entry.get("provider_used", "")
        if prov:
            pmap = key_map[kid]["_providers"]
            pmap[prov] = pmap.get(prov, 0) + 1

    result = []
    for data in key_map.values():
        if data["_domains"]:
            data["top_domain"] = max(
                data["_domains"], key=data["_domains"].get
            )
        if data["_providers"]:
            data["top_provider"] = max(
                data["_providers"], key=data["_providers"].get
            )
        del data["_domains"]
        del data["_providers"]
        result.append(data)
    return sorted(result, key=lambda x: x["calls"], reverse=True)


def _range_to_days(range_str: str) -> int:
    """Convert a range selector value to days."""
    mapping = {"24h": 1, "7d": 7, "30d": 30}
    return mapping.get(range_str, 1)
