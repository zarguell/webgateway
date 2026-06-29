"""JSON-LD extraction strategy.

Extracts all ``<script type="application/ld+json">`` blocks from the HTML,
parses each as JSON, and selects the most relevant one by ``@type`` priority.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from serp_llm.post_processing.strategies import StrategyResult

logger = logging.getLogger(__name__)

# Priority ordering for JSON-LD @type values. Higher = more relevant.
_TYPE_PRIORITY: dict[str, int] = {
    "Product": 100,
    "Recipe": 90,
    "JobPosting": 85,
    "VideoGame": 85,
    "Event": 80,
    "TVSeries": 80,
    "TVEpisode": 80,
    "TVSeason": 80,
    "Movie": 75,
    "Book": 75,
    "MusicAlbum": 75,
    "Episode": 75,
    "PodcastEpisode": 75,
    "MusicRecording": 75,
    "Article": 70,
    "NewsArticle": 70,
    "TechArticle": 70,
    "PodcastSeries": 70,
    "Course": 70,
    "SoftwareApplication": 65,
    "WebApplication": 65,
    "MobileApplication": 65,
    "VideoObject": 65,
    "MusicGroup": 65,
    "MusicPlaylist": 65,
    "LocalBusiness": 60,
    "EducationalOrganization": 60,
    "Periodical": 60,
    "PublicationIssue": 60,
    "AudioObject": 60,
    "Organization": 50,
    "Person": 50,
    "CreativeWork": 50,
    "Painting": 50,
    "Photograph": 50,
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


def _extract_name(val: object) -> str | None:
    """Extract name/string from a JSON-LD value (str, dict, or list of dicts/strings)."""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        name = val.get("name")
        return str(name) if name else None
    if isinstance(val, list):
        items = []
        for v in val:
            if isinstance(v, dict):
                name = v.get("name")
                if name:
                    items.append(str(name))
            elif isinstance(v, str):
                items.append(v)
        return ", ".join(items) if items else None
    return None


def _format_iso8601_duration(duration: str) -> str:
    """Convert ISO 8601 duration like PT2H15M to readable '2h 15m'."""
    import re
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return duration
    days, hours, minutes, seconds = (
        int(m.group(1) or 0),
        int(m.group(2) or 0),
        int(m.group(3) or 0),
        int(m.group(4) or 0),
    )
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    return " ".join(parts) if parts else duration


def flatten_jsonld_to_markdown(data: dict) -> str:
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
    if offers and isinstance(offers, dict):
        availability = offers.get("availability")
        if availability:
            in_stock = "InStock" in str(availability)
            lines.append(f"**Availability:** {'In Stock' if in_stock else 'Check'}")

    # Genre (common across Movie, TVSeries, MusicAlbum, VideoGame, Book)
    genre = data.get("genre")
    genre_name = _extract_name(genre)
    if genre_name:
        lines.append(f"**Genre:** {genre_name}")

    # Content rating (Movie, TVSeries, VideoGame)
    content_rating = data.get("contentRating")
    if content_rating:
        lines.append(f"**Rated:** {content_rating}")

    # Duration (Movie, Episode, PodcastEpisode)
    duration = data.get("duration")
    if duration:
        lines.append(f"**Duration:** {_format_iso8601_duration(duration)}")

    # Director (Movie, TVSeries, Episode)
    director = data.get("director")
    if director:
        director_name = _extract_name(director)
        if director_name:
            lines.append(f"**Director:** {director_name}")

    # Actor / Cast (Movie, TVSeries, Episode)
    actor = data.get("actor")
    if actor:
        actor_name = _extract_name(actor)
        if actor_name:
            lines.append(f"**Cast:** {actor_name}")

    # Creator / Author / Publisher / Producer
    creator = data.get("creator") or data.get("author")
    if creator:
        creator_name = _extract_name(creator)
        if creator_name:
            lines.append(f"**Creator:** {creator_name}")
    publisher = data.get("publisher") or data.get("productionCompany")
    if publisher:
        publisher_name = _extract_name(publisher)
        if publisher_name:
            lines.append(f"**Publisher:** {publisher_name}")

    # By Artist (MusicAlbum)
    by_artist = data.get("byArtist")
    if by_artist:
        artist_name = _extract_name(by_artist)
        if artist_name:
            lines.append(f"**Artist:** {artist_name}")

    # Episode info (TVEpisode, PodcastEpisode)
    ep_number = data.get("episodeNumber")
    season_number = data.get("partOfSeason")
    if isinstance(season_number, dict):
        season_number = season_number.get("seasonNumber")
    part_of_series = data.get("partOfSeries")
    if isinstance(part_of_series, dict):
        series_name = part_of_series.get("name")
        if series_name:
            lines.append(f"**Series:** {series_name}")
    if season_number:
        lines.append(f"**Season:** {season_number}")
    if ep_number:
        lines.append(f"**Episode:** {ep_number}")

    # Application info (VideoGame, SoftwareApplication)
    app_cat = data.get("applicationCategory")
    if app_cat:
        lines.append(f"**Category:** {app_cat}")
    os = data.get("operatingSystem")
    if os:
        lines.append(f"**OS:** {os}")

    # Language
    lang = data.get("inLanguage")
    if lang:
        lines.append(f"**Language:** {lang}")

    # Date
    date = data.get("datePublished")
    if date:
        lines.append(f"**Published:** {date}")

    # Keywords
    keywords = data.get("keywords")
    if keywords:
        lines.append(f"**Keywords:** {keywords}")

    # Key-value pairs for remaining known fields
    for key in ("sku", "brand", "mpn", "isbn", "numberOfPages"):
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
            # Handle @graph (list of blocks) or top-level array
            if isinstance(data, dict) and "@graph" in data:
                blocks = data["@graph"]
            elif isinstance(data, list):
                blocks = data
            else:
                blocks = [data]
            for block in blocks:
                if isinstance(block, dict) and "@type" in block:
                    scored = _score_block(block)
                    if scored:
                        candidates.append(scored)

        if not candidates:
            return None

        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        markdown = flatten_jsonld_to_markdown(best.data)

        return StrategyResult(
            content=markdown,
            format="markdown",
            structured_data=best.data,
        )
