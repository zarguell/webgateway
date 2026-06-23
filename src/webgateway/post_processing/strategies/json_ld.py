"""JSON-LD extraction strategy.

Extracts all ``<script type="application/ld+json">`` blocks from the HTML,
parses each as JSON, and selects the most relevant one by ``@type`` priority.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from webgateway.post_processing.strategies import StrategyResult

logger = logging.getLogger(__name__)

# Priority ordering for JSON-LD @type values. Higher = more relevant.
_TYPE_PRIORITY: dict[str, int] = {
    "Product": 100,
    "Recipe": 90,
    "JobPosting": 85,
    "Event": 80,
    "Movie": 75,
    "Book": 75,
    "Article": 70,
    "NewsArticle": 70,
    "TechArticle": 70,
    "SoftwareApplication": 65,
    "LocalBusiness": 60,
    "Organization": 50,
    "Person": 50,
    "Review": 45,
    "FAQPage": 40,
    "WebPage": 10,
    "WebSite": 5,
    "BreadcrumbList": 3,
    "SearchAction": 1,
}

_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class _ScoredBlock:
    data: dict
    score: int
    type_name: str


def _score_block(data: dict) -> _ScoredBlock | None:
    """Score a JSON-LD block by its @type and field count."""
    type_val = data.get("@type", "")
    if isinstance(type_val, str):
        primary = type_val
    elif isinstance(type_val, list) and type_val:
        primary = type_val[0]
    else:
        return None

    base_score = _TYPE_PRIORITY.get(primary, 20)
    field_count = len(data) - 1  # exclude @context
    score = base_score + min(field_count, 50)
    return _ScoredBlock(data=data, score=score, type_name=primary)


def _flatten_to_markdown(data: dict) -> str:
    """Flatten a JSON-LD block into readable markdown."""
    lines: list[str] = []
    type_name = data.get("@type", "")
    if isinstance(type_name, list):
        type_name = type_name[0]

    name = data.get("name", data.get("headline", ""))
    if name:
        lines.append(f"# {name}")

    desc = data.get("description", "")
    if desc:
        lines.append("")
        lines.append(desc)

    # Price
    offers = data.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
        currency = offers.get("priceCurrency", "")
        if price is not None:
            lines.append("")
            lines.append(f"**Price:** {currency} {price}")
    elif isinstance(offers, list) and offers:
        prices = [o.get("price") for o in offers if isinstance(o, dict)]
        if prices:
            lines.append("")
            lines.append(f"**Price range:** {' – '.join(str(p) for p in prices if p)}")

    # Rating
    agg_rating = data.get("aggregateRating")
    if isinstance(agg_rating, dict):
        rating = agg_rating.get("ratingValue")
        count = agg_rating.get("reviewCount")
        if rating:
            suffix = f" ({count} reviews)" if count else ""
            lines.append(f"**Rating:** {rating}/5{suffix}")

    # Availability
    if offers:
        availability = (
            offers.get("availability") if isinstance(offers, dict) else None
        )
        if availability:
            in_stock = "InStock" in str(availability)
            lines.append(f"**Availability:** {'In Stock' if in_stock else 'Check'}")

    # Author
    author = data.get("author")
    if isinstance(author, dict):
        author_name = author.get("name")
        if author_name:
            lines.append(f"**Author:** {author_name}")

    # Date
    date = data.get("datePublished")
    if date:
        lines.append(f"**Published:** {date}")

    # Key-value pairs for remaining known fields
    for key in ("sku", "brand", "mpn", "isbn"):
        val = data.get(key)
        if val:
            if isinstance(val, dict):
                val = val.get("name", str(val))
            lines.append(f"**{key.capitalize()}:** {val}")

    return "\n".join(lines).strip()


class JsonLdStrategy:
    """Extract structured data from JSON-LD script blocks."""

    async def extract(self, html: str, url: str) -> StrategyResult | None:
        """Find and extract the best JSON-LD block from *html*."""
        matches = _JSONLD_RE.findall(html)
        if not matches:
            return None

        candidates: list[_ScoredBlock] = []
        for raw in matches:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Handle @graph (list of blocks)
            blocks = data if isinstance(data, list) else [data]
            for block in blocks:
                if isinstance(block, dict) and "@type" in block:
                    scored = _score_block(block)
                    if scored:
                        candidates.append(scored)

        if not candidates:
            return None

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        markdown = _flatten_to_markdown(best.data)

        return StrategyResult(
            content=markdown,
            format="markdown",
            structured_data=best.data,
        )
