"""Meta tag extraction strategy.

Extracts Open Graph, Twitter Card, and standard meta tags from HTML ``<head>``.
Useful as a quick summary strategy for news articles, blog posts, and pages
where JSON-LD is sparse.
"""

from __future__ import annotations

import logging
import re

from serp_llm.post_processing.strategies import StrategyResult

logger = logging.getLogger(__name__)

_META_RE = re.compile(
    r'<meta[^>]+>',
    re.DOTALL | re.IGNORECASE,
)

_NAME_RE = re.compile(
    r'(?:name|property)="([^"]+)"',
    re.IGNORECASE,
)

_CONTENT_RE = re.compile(
    r'content="([^"]*)"',
    re.IGNORECASE,
)

_TITLE_RE = re.compile(
    r'<title[^>]*>([^<]+)</title>',
    re.DOTALL | re.IGNORECASE,
)

# Priority ordering for meta tags in the output. Higher = more relevant.
_PRIORITY: dict[str, int] = {
    "og:title": 100,
    "twitter:title": 90,
    "og:description": 85,
    "twitter:description": 80,
    "description": 75,
    "og:image": 70,
    "twitter:image": 65,
    "og:url": 60,
    "twitter:card": 55,
    "og:site_name": 50,
    "og:type": 45,
    "article:published_time": 40,
    "article:author": 35,
    "article:section": 30,
    "twitter:site": 25,
    "twitter:creator": 20,
    "keywords": 15,
    "author": 10,
}


def _extract_meta_tags(html: str) -> dict[str, str]:
    """Extract all meta tags from HTML into a flat dict of name→content."""
    result: dict[str, str] = {}
    for tag in _META_RE.findall(html):
        name_match = _NAME_RE.search(tag)
        content_match = _CONTENT_RE.search(tag)
        if name_match and content_match:
            name = name_match.group(1).strip()
            content = content_match.group(1).strip()
            if name and content and name not in result:
                result[name] = content
    return result


def _flatten_to_markdown(meta: dict[str, str], title: str | None) -> str:
    """Flatten meta tags into readable markdown."""
    lines: list[str] = []

    if title:
        lines.append(f"# {title}")

    og_title = meta.get("og:title") or meta.get("twitter:title")
    if og_title and og_title != title:
        lines.append(f"**{og_title}**")

    desc = (
        meta.get("og:description")
        or meta.get("twitter:description")
        or meta.get("description")
    )
    if desc:
        lines.append("")
        lines.append(desc)

    # Ordered key-value pairs for remaining tags
    sorted_tags = sorted(meta.items(), key=lambda kv: _PRIORITY.get(kv[0], 0), reverse=True)
    for name, content in sorted_tags:
        # Skip tags already rendered above
        if name in (
        "og:title", "twitter:title", "og:description",
        "twitter:description", "description",
    ):
            continue
        label = (
            name.replace("og:", "")
            .replace("twitter:", "")
            .replace("_", " ")
            .replace(":", " ")
            .title()
        )
        lines.append(f"**{label}:** {content}")

    return "\n".join(lines).strip()


class MetaExtractStrategy:
    """Extract meta tags (OG, Twitter, standard) from HTML."""

    async def extract(self, html: str, url: str) -> StrategyResult | None:
        """Find and extract meta tags from *html*."""
        meta = _extract_meta_tags(html)
        title_match = _TITLE_RE.search(html)
        title = title_match.group(1).strip() if title_match else None

        if not meta and not title:
            return None

        markdown = _flatten_to_markdown(meta, title)

        return StrategyResult(
            content=markdown,
            format="markdown",
            structured_data=meta,
        )
