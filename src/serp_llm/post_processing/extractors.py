from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.DOTALL | re.IGNORECASE)

_STOP_WORDS = frozenset(
    [
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "is",
        "it", "its", "this", "that", "with", "from", "by", "as", "was", "were", "be",
        "been", "has", "have", "had", "not", "no", "do", "does", "did", "will",
        "would", "could", "should", "may", "can", "all", "each", "every", "both",
        "few", "more", "most", "other", "some", "such", "than", "too", "very",
        "just", "about", "above", "after", "again", "against", "between", "into",
        "through", "during", "before", "below", "up", "down", "out", "off", "over",
        "under", "same", "so", "what", "when", "where", "why", "how", "all", "any",
        "here", "there", "also", "only", "own", "now", "then", "still", "your",
        "my", "his", "her", "their", "our", "its",
    ]
)


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


def _score_content(text: str) -> int:
    """Score extracted content by the total length of substantial lines.

    A 'substantial' line is one with 40+ characters of actual text,
    excluding bare URLs, markdown images, and table-divider lines.
    This distinguishes real article content from navigation,
    cookie banners, and other boilerplate.
    """
    if not text:
        return 0
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    score = 0
    for line in clean.split("\n"):
        stripped = re.sub(r" +", " ", line.strip())
        stripped = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", stripped)
        if len(stripped) < 40:
            continue
        if stripped.startswith(("http", "![", "[!", "|", "---")):
            continue
        score += len(stripped)
    return score


def _title_keywords(html: str) -> list[str]:
    """Extract significant keywords from the page <title>.

    Returns up to 3 words (≥3 chars, not stop words), longest first.
    Returns empty list if no title found.
    """
    match = _TITLE_RE.search(html)
    if not match:
        return []
    title = match.group(1).strip()
    words = re.findall(r"[a-zA-Z]{3,}", title)
    significant = [w.lower() for w in words if w.lower() not in _STOP_WORDS]
    significant.sort(key=len, reverse=True)
    return significant[:3]


def _content_has_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"https?://\S+", "", clean)
    return any(kw in clean.lower() for kw in keywords)


def _content_has_keywords_early(text: str, keywords: list[str], limit: int = 300) -> bool:
    if not keywords:
        return True
    head = re.sub(r"<[^>]+>", " ", text[:limit])
    head = re.sub(r"https?://\S+", "", head)
    return any(kw in head.lower() for kw in keywords)


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
        tf_result = trafilatura_extract(html, url)
        rd_result = readability_extract(html)
        tf_score = _score_content(tf_result) if tf_result else 0
        rd_score = _score_content(rd_result) if rd_result else 0

        readability_wins = rd_score > tf_score * 1.2

        # Title-aware override: prefer the extractor whose early content
        # contains the page title — focused content puts the subject at the top,
        # while sidebar-heavy extraction buries it in navigation.
        keywords = _title_keywords(html)
        tf_has_title_early = (
            _content_has_keywords_early(tf_result, keywords) if tf_result else False
        )
        rd_has_title_early = (
            _content_has_keywords_early(rd_result, keywords) if rd_result else False
        )

        if tf_result and tf_has_title_early and len(tf_result) >= min_content_length:
            return tf_result, "markdown", False
        if rd_result and rd_has_title_early and len(rd_result) >= min_content_length:
            return rd_result, "html", True
        if readability_wins and rd_result and len(rd_result) >= min_content_length:
            return rd_result, "html", True
        if tf_result and len(tf_result) >= min_content_length:
            return tf_result, "markdown", False
        if rd_result and len(rd_result) >= min_content_length:
            return rd_result, "html", True
        logger.info(
            "extraction fallback for %s: trafilatura=%s readability=%s",
            url,
            len(tf_result) if tf_result else 0,
            len(rd_result) if rd_result else 0,
        )
        return html, "html", True

    if extractor == "readability":
        result = readability_extract(html)
        if result is not None and len(result) >= min_content_length:
            return result, "html", False
        return html, "html", True

    return html, "html", False
