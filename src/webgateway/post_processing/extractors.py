from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def trafilatura_extract(html: str, url: str) -> str | None:
    import trafilatura
    try:
        result = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            no_fallback=False,
        )
        return result if result else None
    except Exception as exc:
        logger.warning("trafilatura extraction failed for %s: %s", url, exc)
        return None


def readability_extract(html: str) -> str | None:
    from readability import Document
    try:
        doc = Document(html)
        summary = doc.summary()
        return summary if summary.strip() else None
    except Exception as exc:
        logger.warning("readability extraction failed: %s", exc)
        return None


def extract_main_content(
    html: str,
    url: str,
    *,
    extractor: str = "trafilatura",
    min_content_length: int = 200,
) -> tuple[str, str, bool]:
    if extractor == "none":
        return html, "html", False

    if extractor == "trafilatura":
        result = trafilatura_extract(html, url)
        if result is not None and len(result) >= min_content_length:
            return result, "markdown", False
        readability_result = readability_extract(html)
        if readability_result is not None and len(readability_result) >= min_content_length:
            return readability_result, "html", True
        logger.info(
            "extraction fallback for %s: trafilatura=%s readability=%s",
            url,
            len(result) if result else 0,
            len(readability_result) if readability_result else 0,
        )
        return html, "html", True

    if extractor == "readability":
        result = readability_extract(html)
        if result is not None and len(result) >= min_content_length:
            return result, "html", False
        return html, "html", True

    return html, "html", False
