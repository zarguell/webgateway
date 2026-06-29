"""Lightweight sidecar that bridges serpLLM to a host Chrome via CDP.

Connects to an existing Chrome instance (no browser binary, no playwright
install). Extracts page content and converts to markdown with trafilatura.

Endpoints:
  GET  /health   — health check
  POST /extract  — navigate to a URL, return page content
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("cdp_chrome")

CDP_URL = os.environ.get("CHROME_CDP_URL", "http://127.0.0.1:9224")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    url: str
    timeout: int = 30


class ExtractResponse(BaseModel):
    content: str
    format: str = "markdown"
    url: str
    title: str | None = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="cdp-chrome REST API", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest):
    if not req.url:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = await context.new_page()

            await page.goto(
                req.url,
                wait_until="domcontentloaded",
                timeout=req.timeout * 1000,
            )

            title = await page.title()
            raw_html = await page.content()
            await page.close()

    except Exception as exc:  # noqa: BLE001 — top-level HTTP endpoint boundary
        logger.exception("Extract failed for %s", req.url)
        detail = str(exc)
        if "refused" in detail.lower() or "connect" in detail.lower():
            raise HTTPException(status_code=503, detail="Chrome not connected") from exc
        raise HTTPException(status_code=502, detail=f"Extract failed: {exc}") from exc

    return ExtractResponse(
        content=raw_html,
        format="html",
        url=req.url,
        title=title or None,
    )
