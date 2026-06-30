"""REST API wrapper for invisible_playwright.

Exposes the stealth browser as a lightweight HTTP sidecar so the serpLLM
can scrape pages through the C++-patched Firefox without importing the library
directly.

Endpoints:
  GET  /health  — health check (returns 200 when ready)
  POST /scrape  — navigate to a URL, extract page content
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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
    text_mode: bool = False


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

    try:
        from invisible_playwright.async_api import InvisiblePlaywright

        async with InvisiblePlaywright(
            seed=seed,
            proxy=proxy,
            headless=True,
            humanize=False,  # skip bezier mouse for speed
        ) as browser:
            page = await browser.new_page(
                viewport={"width": 1920, "height": 1080},
            )

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

            # Expand collapsed content: scroll to trigger lazy loading, then
            # uncover hidden sections by clicking "show more" buttons and removing
            # CSS constraints on common collapsed containers.  This runs before
            # extraction so innerText captures content that was behind interactions.
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_load_state("networkidle")
                # Click common "show more" / "read more" buttons
                for selector in (
                    'button:has-text("show more")',
                    'button:has-text("Show more")',
                    'button:has-text("Read more")',
                    'button:has-text("Load more")',
                    'button:has-text("View more")',
                    'button:has-text("See more")',
                    '[class*="show-more"] button',
                    '[class*="read-more"] button',
                ):
                    buttons = await page.query_selector_all(selector)
                    for btn in buttons:
                        try:
                            await btn.click()
                            await page.wait_for_timeout(200)
                        except Exception:
                            pass
                # Force-reveal CSS-hidden content within content containers,
                # scoped to main content area to avoid global layout breakage.
                await page.evaluate("""() => {
                    const root = document.querySelector('main, article, [role="main"]')
                        || document.body;
                    for (const el of root.querySelectorAll(
                        '[class*="collapsed"], [class*="folded"], ' +
                        '[class*="truncated"], [style*="max-height"], ' +
                        '.read-more, .show-more, .hidden-text'
                    )) {
                        el.style.maxHeight = 'none';
                        el.style.overflow = 'visible';
                        el.style.display = 'block';
                    }
                }""")
            except Exception:
                logger.debug("Content expansion skipped", exc_info=True)

            title = await page.title()

            # Return content: raw HTML for the pipeline to process (strategy extraction,
            # trafilatura), or clean visible text when text_mode is requested.  Text mode
            # uses document.body.innerText — the browser's built-in "select all → copy as
            # plain text" — which strips scripts, styles, tracking, nav, and sidebars
            # without needing any HTML parsing or PDF conversion.
            if req.text_mode:
                content = await page.evaluate("() => document.body.innerText")
                fmt = "text"
            else:
                content = await page.content()
                fmt = "html"

            return ScrapeResponse(
                content=content,
                format=fmt,
                url=req.url,
                title=title or None,
            )

    except Exception as exc:
        logger.exception("Scrape failed for %s", req.url)
        raise HTTPException(status_code=502, detail=f"Scrape failed: {exc}") from exc