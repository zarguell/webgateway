"""FastAPI application factory and lifespan wiring.

Creates the ``app`` object, initialises all subsystems in an async lifespan
context manager, wires route routers, and exposes root status / docs redirects.

Run with::

    uvicorn webgateway.main:app --reload
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from webgateway.admin_session import AdminSessionManager
from webgateway.alerting import AlertDispatcher
from webgateway.audit import AuditLogger
from webgateway.cache.store import CacheStore
from webgateway.config import ConfigManager
from webgateway.dlp import DlpBlockedError, DlpMiddleware
from webgateway.injection.detector import InjectionDetector
from webgateway.injection.events import EventLogger
from webgateway.injection.types import InjectionBlockedError
from webgateway.judge import LLMJudge
from webgateway.key_store import KeyStore
from webgateway.mcp.server import mount_mcp
from webgateway.policy.engine import PolicyEngine
from webgateway.post_processing.dedup import DedupStore
from webgateway.post_processing.pipeline import PostProcessingPipeline
from webgateway.post_processing.strategies import StrategySelector
from webgateway.post_processing.strategies.json_ld import JsonLdStrategy
from webgateway.post_processing.strategies.meta_extract import MetaExtractStrategy
from webgateway.providers.base import ProviderError
from webgateway.providers.registry import ProviderRegistry
from webgateway.proxy import ProxyResolver
from webgateway.ratelimit.limiter import SlidingWindowRateLimiter
from webgateway.ratelimit.middleware import RateLimitMiddleware
from webgateway.resource_manager import ProviderResourceManager
from webgateway.routes.admin import router as admin_router
from webgateway.routes.admin_ui import router as admin_ui_router
from webgateway.routes.cache import router as cache_router
from webgateway.routes.extract import router as extract_router
from webgateway.routes.health import router as health_router
from webgateway.routes.keys import router as keys_router
from webgateway.routes.providers import router as providers_router
from webgateway.routes.search import router as search_router
from webgateway.routes.sessions_admin import router as sessions_admin_router
from webgateway.service import GatewayService
from webgateway.sessions.manager import SessionError, SessionManager
from webgateway.sessions.store import SessionStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise all subsystems on startup.

    The ConfigManager is created first — every other component depends on it.
    All services are stored on ``app.state`` so route handlers can access them
    via ``request.app.state`` (explicit wiring, no global mutable state).
    """
    load_dotenv()

    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    config_manager = ConfigManager(config_path)
    app.state.config_manager = config_manager

    # --- Rate limiting (background bucket cleanup) ---
    rate_limiter = SlidingWindowRateLimiter(config_manager.config.rate_limiting)
    app.state.rate_limiter = rate_limiter
    await rate_limiter.start_background_cleanup()

    policy_engine = PolicyEngine(config_manager)
    app.state.policy_engine = policy_engine

    proxy_resolver = ProxyResolver(config_manager.config.proxies)
    app.state.proxy_resolver = proxy_resolver

    audit_logger = AuditLogger(config_manager.config.logging)
    app.state.audit_logger = audit_logger

    provider_registry = ProviderRegistry(config_manager)
    app.state.provider_registry = provider_registry

    cache_store = CacheStore(config_manager.config.cache.db_path)
    app.state.cache_store = cache_store

    dlp_middleware = DlpMiddleware(
        [p.model_dump() for p in config_manager.config.dlp_policies]
    )
    app.state.dlp_middleware = dlp_middleware

    # --- Events + Alerting (PRD §18.7) ---
    events_path = os.environ.get("EVENTS_PATH", "/app/logs/events.jsonl")
    alert_dispatcher = AlertDispatcher(config_manager.config.alerts)
    event_logger = EventLogger(
        events_path=events_path, alert_dispatcher=alert_dispatcher
    )
    app.state.event_logger = event_logger

    resource_manager = ProviderResourceManager(
        db_path="data/resource_manager.db",
        config=config_manager.config,
        event_logger=event_logger,
    )
    app.state.resource_manager = resource_manager

    # --- Key store (SQLite-backed API keys) ---
    key_store = KeyStore(db_path="data/api_keys.db")
    app.state.key_store = key_store

    # --- Admin session manager (signed cookies) ---
    admin_session_mgr = AdminSessionManager(
        secret=os.environ.get("ADMIN_SESSION_SECRET")
    )
    app.state.admin_session_manager = admin_session_mgr

    # --- Session store ---
    encryption_key = config_manager.config.sessions.encryption_key
    if encryption_key:
        session_store = SessionStore(
            store_path=config_manager.config.sessions.store_path,
            encryption_key=encryption_key,
        )
        session_manager = SessionManager(
            session_store,
            config_manager.config.sessions,
        )
    else:
        session_store = None
        session_manager = None
    app.state.session_store = session_store
    app.state.session_manager = session_manager

    # --- Prompt injection detector (optional, PRD §27) ---
    injection_detector: InjectionDetector | None = None
    pi_config = config_manager.config.prompt_injection
    if pi_config.enabled:
        injection_detector = InjectionDetector(pi_config)
        app.state.injection_detector = injection_detector

    # --- Extraction strategies ---
    strategy_selector = StrategySelector(config_manager)
    strategy_selector.register("json_ld", JsonLdStrategy())
    strategy_selector.register("meta_extract", MetaExtractStrategy())
    app.state.strategy_selector = strategy_selector

    # --- Post-processing pipeline ---
    dedup_store = None
    pp_config = config_manager.config.post_processing
    if pp_config.deduplication.enabled:
        dedup_store = DedupStore(db_path="data/dedup.db")
    post_processing = PostProcessingPipeline(
        config=pp_config,
        strategy_selector=strategy_selector,
        dedup_store=dedup_store,
        injection_detector=injection_detector,
    )
    app.state.dedup_store = dedup_store
    app.state.post_processing = post_processing

    # --- LLM Judge (optional, Tier 2 routing) ---
    llm_judge: LLMJudge | None = None
    if config_manager.config.llm_judge.enabled:
        llm_judge = LLMJudge(config_manager, provider_registry)
        app.state.llm_judge = llm_judge

    gateway_service = GatewayService(
        config_manager,
        policy_engine,
        provider_registry,
        proxy_resolver,
        audit_logger,
        cache_store=cache_store,
        dlp_middleware=dlp_middleware,
        resource_manager=resource_manager,
        session_manager=session_manager,
        post_processing=post_processing,
        llm_judge=llm_judge,
        injection_detector=injection_detector,
        event_logger=event_logger,
    )
    app.state.gateway_service = gateway_service

    # --- MCP server (optional) ---
    mcp_ctx = mount_mcp(app, gateway_service, config_manager)
    if mcp_ctx:
        async with mcp_ctx:
            yield
    else:
        yield

    # Cleanup rate limiter background task
    if hasattr(app.state, "rate_limiter"):
        await app.state.rate_limiter.stop_background_cleanup()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="WebGateway",
        version="0.1.0",
        description="Self-hosted, policy-driven web search and extraction gateway for AI agents.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # --- Mount static files (MkDocs output) ---
    static_dir = os.environ.get("STATIC_DIR", "static")
    static_path = os.path.join(os.path.dirname(__file__), "..", "..", static_dir)
    if os.path.isdir(static_path):
        app.mount(
            "/docs",
            StaticFiles(directory=static_path, html=True),
            name="docs",
        )
        # Mount MkDocs assets at /docs/assets
        docs_static = os.path.join(static_path, "docs")
        if os.path.isdir(docs_static):
            app.mount(
                "/docs",
                StaticFiles(directory=docs_static, html=True),
                name="mkdocs",
            )

    # --- Routers ---
    app.include_router(search_router)
    app.include_router(extract_router)
    app.include_router(health_router)
    app.include_router(providers_router)
    app.include_router(admin_router)
    app.include_router(cache_router)
    app.include_router(sessions_admin_router)
    app.include_router(keys_router)
    app.include_router(admin_ui_router)

    # --- Rate limiting middleware ---
    app.add_middleware(RateLimitMiddleware)

    # --- Exception handlers ---
    @app.exception_handler(ProviderError)
    async def provider_error_handler(
        request: Request, exc: ProviderError
    ) -> JSONResponse:
        http_status = 429 if exc.status_code == 429 else 502
        return JSONResponse(
            status_code=http_status,
            content={
                "error": {
                    "provider": exc.provider,
                    "message": str(exc),
                    "upstream_status": exc.status_code,
                }
            },
        )

    @app.exception_handler(DlpBlockedError)
    async def dlp_block_handler(
        request: Request, exc: DlpBlockedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "message": str(exc),
                    "policy": exc.policy,
                    "matched_rules": exc.match_names,
                }
            },
        )

    @app.exception_handler(InjectionBlockedError)
    async def injection_block_handler(
        request: Request, exc: InjectionBlockedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": "prompt_injection_detected",
                "url": exc.url,
                "injection_type": exc.injection_type,
                "action_taken": "block",
                "message": "Content blocked: prompt injection detected",
            },
        )

    @app.exception_handler(SessionError)
    async def session_error_handler(
        request: Request, exc: SessionError
    ) -> JSONResponse:
        status_map = {
            "session_expired": 419,
            "session_not_found": 404,
        }
        http_status = status_map.get(exc.error_class, 400)
        return JSONResponse(
            status_code=http_status,
            content={
                "error": {
                    "error_class": exc.error_class,
                    "session_id": exc.session_id,
                    "message": str(exc),
                }
            },
        )

    @app.get("/")
    async def root():
        """Root endpoint — redirects to the MkDocs docs site."""
        return RedirectResponse(url="/docs")

    return app


app = create_app()
