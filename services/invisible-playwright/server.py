"""REST API wrapper for invisible_playwright.

Exposes the stealth browser as a lightweight HTTP sidecar so the WebGateway
can scrape pages through the C++-patched Firefox without importing the library
directly.

Endpoints:
  GET  /health  — health check (returns 200 when ready)
  POST /scrape  — navigate to a URL, extract page content
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("invisible_playwright")

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ScrapeRequest(BaseModel):
    url: str
    proxy: str | None = None
    fingerprint: str | None = None
    session_id: str | None = None
    cookies: list[dict] | None = None
    user_agent: str | None = None
    wait_for_selector: str | None = None
    timeout: int = 30_000


class ScrapeResponse(BaseModel):
    content: str
    format: str = "markdown"
    url: str
    title: str | None = None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure the patched Firefox binary is cached."""
    from invisible_playwright import ensure_binary
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ensure_binary())
    logger.info("invisible_playwright binary ready")
    yield


app = FastAPI(
    title="invisible_playwright REST API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape(req: ScrapeRequest):
    """Navigate to *req.url* with the stealth browser and return page content."""
    if not req.url:
        raise HTTPException(status_code=400, detail="url is required")

    # Build proxy config dict if a URL was provided
    proxy = {"server": req.proxy} if req.proxy else None

    # Derive a seed from fingerprint_id for reproducible profiles
    seed = None
    if req.fingerprint and req.fingerprint != "rotate":
        try:
            seed = int(req.fingerprint.removeprefix("fp_"))
        except (ValueError, AttributeError):
            seed = abs(hash(req.fingerprint))

    timeout_s = max(req.timeout / 1000.0, 10.0)

    try:
        from invisible_playwright.async_api import InvisiblePlaywright

        async with InvisiblePlaywright(
            seed=seed,
            proxy=proxy,
            headless=True,
            humanize=False,  # skip bezier mouse for speed
        ) as browser:
            page = await browser.new_page()

            # Inject cookies if provided
            if req.cookies:
                try:
                    await page.context.add_cookies(req.cookies)
                except Exception as exc:
                    logger.warning("Failed to inject cookies: %s", exc)

            # Override user-agent if specified
            if req.user_agent:
                await page.set_extra_http_headers({"User-Agent": req.user_agent})

            # Navigate
            await page.goto(
                req.url,
                wait_until="domcontentloaded",
                timeout=req.timeout,
            )

            # Wait for a specific selector if requested
            if req.wait_for_selector:
                try:
                    await page.wait_for_selector(
                        req.wait_for_selector,
                        timeout=min(req.timeout, 15_000),
                    )
                except Exception:
                    logger.warning(
                        "Selector %r not found within timeout", req.wait_for_selector
                    )

            title = await page.title()

            # Extract text content (approximate markdown)
            content = await page.evaluate(
                """() => {
                    const main = document.querySelector('article')
                        || document.querySelector('[role="main"]')
                        || document.querySelector('main')
                        || document.body;
                    return main.innerText;
                }"""
            )

            return ScrapeResponse(
                content=content.strip(),
                format="markdown",
                url=req.url,
                title=title or None,
            )

    except Exception as exc:
        logger.exception("Scrape failed for %s", req.url)
        raise HTTPException(status_code=502, detail=f"Scrape failed: {exc}")
