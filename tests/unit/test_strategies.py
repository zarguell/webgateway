"""Unit tests for extraction strategies."""

from __future__ import annotations

import pytest

from serp_llm.post_processing.strategies.json_ld import (
    JsonLdStrategy,
    _extract_name,
    _format_iso8601_duration,
)
from serp_llm.post_processing.strategies.meta_extract import MetaExtractStrategy


@pytest.fixture
def strategy() -> JsonLdStrategy:
    return JsonLdStrategy()


class TestJsonLdStrategy:
    async def test_extract_product_json_ld(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Test Product",
            "description": "A test product",
            "offers": {
                "@type": "Offer",
                "price": "29.99",
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock"
            },
            "aggregateRating": {
                "@type": "AggregateRating",
                "ratingValue": "4.5",
                "reviewCount": "123"
            },
            "brand": {"@type": "Brand", "name": "TestBrand"},
            "sku": "TST-001"
        }
        </script>
        </head><body><p>Some content</p></body></html>
        """
        result = await strategy.extract(html, "https://example.com/product")
        assert result is not None
        assert "Test Product" in result.content
        assert "29.99" in result.content
        assert "4.5" in result.content
        assert "In Stock" in result.content
        assert result.structured_data is not None
        assert result.structured_data["@type"] == "Product"

    async def test_no_json_ld_returns_none(self, strategy: JsonLdStrategy):
        html = "<html><body><p>No structured data here</p></body></html>"
        result = await strategy.extract(html, "https://example.com")
        assert result is None

    async def test_empty_json_ld_returns_none(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json"></script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com")
        assert result is None

    async def test_malformed_json_ld_ignored(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">{invalid</script>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Valid Product"}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com")
        assert result is not None
        assert "Valid Product" in result.content

    async def test_higher_priority_type_wins(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "WebPage", "name": "Generic Page"}
        </script>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Real Product", "description": "desc"}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com")
        assert result is not None
        assert "Real Product" in result.content
        assert "Generic Page" not in result.content

    async def test_article_flattens_to_readable_markdown(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Recipe",
            "name": "Test Recipe",
            "description": "A delicious recipe",
            "author": {"@type": "Person", "name": "Chef"},
            "datePublished": "2024-01-15"
        }
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/recipe")
        assert result is not None
        assert "# Test Recipe" in result.content
        assert "Chef" in result.content
        assert "2024-01-15" in result.content

    async def test_strategy_result_has_structured_data(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "JSON Product", "price": "9.99"}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/p")
        assert result is not None
        assert result.structured_data is not None
        assert result.structured_data["name"] == "JSON Product"

    async def test_multiple_types_in_list(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": ["Product", "Book"], "name": "Multi-Type Item", "isbn": "123"}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/multi")
        assert result is not None
        assert "Multi-Type Item" in result.content

    async def test_handles_graph_array(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@graph": [
            {"@type": "WebPage", "name": "Page"},
            {"@type": "Product", "name": "Graph Product", "description": "from @graph"}
        ]}
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/graph")
        assert result is not None
        assert "Graph Product" in result.content

    async def test_tvseries_extracts_genre_and_cast(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "TVSeries",
            "name": "Stranger Things",
            "description": "A group of kids uncover supernatural mysteries.",
            "genre": ["Sci-Fi", "Horror", "Drama"],
            "contentRating": "TV-14",
            "actor": [
                {"@type": "Person", "name": "Millie Bobby Brown"},
                {"@type": "Person", "name": "David Harbour"}
            ],
            "creator": {"@type": "Person", "name": "The Duffer Brothers"},
            "aggregateRating": {
                "@type": "AggregateRating",
                "ratingValue": "8.7",
                "reviewCount": "1200"
            }
        }
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/tv")
        assert result is not None
        assert "Stranger Things" in result.content
        assert "Sci-Fi" in result.content
        assert "TV-14" in result.content
        assert "Millie Bobby Brown" in result.content
        assert "David Harbour" in result.content
        assert "The Duffer Brothers" in result.content
        assert "8.7" in result.content

    async def test_tvepisode_with_season_episode_numbers(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "TVEpisode",
            "name": "The Battle of Winterfell",
            "description": "The living make their final stand.",
            "episodeNumber": 3,
            "partOfSeason": {"@type": "TVSeason", "seasonNumber": 8},
            "partOfSeries": {"@type": "TVSeries", "name": "Game of Thrones"},
            "duration": "PT1H22M",
            "contentRating": "TV-MA"
        }
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/episode")
        assert result is not None
        assert "The Battle of Winterfell" in result.content
        assert "Game of Thrones" in result.content
        assert "3" in result.content
        assert "8" in result.content
        assert "1h 22m" in result.content
        assert "TV-MA" in result.content

    async def test_musicalbum_extracts_artist_and_genre(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "MusicAlbum",
            "name": "Thriller",
            "description": "The best-selling album of all time.",
            "byArtist": {"@type": "MusicGroup", "name": "Michael Jackson"},
            "genre": "Pop",
            "datePublished": "1982-11-30",
            "inLanguage": "en"
        }
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/album")
        assert result is not None
        assert "Thriller" in result.content
        assert "Michael Jackson" in result.content
        assert "Pop" in result.content
        assert "1982-11-30" in result.content
        assert "English" in result.content or "en" in result.content

    async def test_videogame_extracts_platform_and_rating(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "VideoGame",
            "name": "Elden Ring",
            "description": "An action RPG from FromSoftware.",
            "applicationCategory": "Game",
            "operatingSystem": "Windows, PlayStation, Xbox",
            "genre": "Action RPG",
            "contentRating": "Mature",
            "offers": {
                "@type": "Offer",
                "price": "59.99",
                "priceCurrency": "USD"
            }
        }
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/game")
        assert result is not None
        assert "Elden Ring" in result.content
        assert "Action RPG" in result.content
        assert "Game" in result.content
        assert "Windows" in result.content
        assert "59.99" in result.content

    async def test_creativework_fallback(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "CreativeWork",
            "name": "A Generic Work",
            "description": "Fallback for lesser-known types.",
            "author": [
                {"@type": "Person", "name": "Author One"},
                {"@type": "Person", "name": "Author Two"}
            ],
            "datePublished": "2024-06-01",
            "inLanguage": "en"
        }
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/work")
        assert result is not None
        assert "A Generic Work" in result.content
        assert "Author One" in result.content
        assert "Author Two" in result.content


class TestJsonLdHelpers:
    def test_extract_name_from_string(self):
        assert _extract_name("Pop") == "Pop"

    def test_extract_name_from_dict(self):
        assert _extract_name({"@type": "Person", "name": "Director"}) == "Director"

    def test_extract_name_from_list(self):
        names = [{"name": "Actor A"}, {"name": "Actor B"}]
        assert _extract_name(names) == "Actor A, Actor B"

    def test_extract_name_returns_none(self):
        assert _extract_name(None) is None
        assert _extract_name([]) is None

    def test_format_duration_standard(self):
        assert _format_iso8601_duration("PT2H15M") == "2h 15m"

    def test_format_duration_hours_only(self):
        assert _format_iso8601_duration("PT1H") == "1h"

    def test_format_duration_with_seconds(self):
        assert _format_iso8601_duration("PT1H30M20S") == "1h 30m 20s"

    def test_format_duration_with_days(self):
        assert _format_iso8601_duration("P1DT2H") == "1d 2h"

    def test_format_duration_invalid_returns_original(self):
        assert _format_iso8601_duration("unknown") == "unknown"

    def test_format_duration_minutes_only(self):
        assert _format_iso8601_duration("PT30M") == "30m"

    async def test_multiple_authors_via_strategy(self, strategy: JsonLdStrategy):
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Book",
            "name": "Multi-Author Book",
            "author": [
                {"@type": "Person", "name": "Author A"},
                {"@type": "Person", "name": "Author B"}
            ],
            "isbn": "1234567890"
        }
        </script>
        </head></html>
        """
        result = await strategy.extract(html, "https://example.com/book")
        assert result is not None
        assert "Author A, Author B" in result.content
        assert "1234567890" in result.content


@pytest.fixture
def meta_strategy() -> MetaExtractStrategy:
    return MetaExtractStrategy()


class TestMetaExtractStrategy:
    async def test_extract_og_tags(self, meta_strategy: MetaExtractStrategy):
        html = """
        <html><head>
        <title>Test Article</title>
        <meta property="og:title" content="OG Test Article">
        <meta property="og:description" content="An OG description">
        <meta property="og:image" content="https://example.com/image.jpg">
        <meta name="description" content="A meta description">
        <meta name="keywords" content="test, article">
        </head><body><p>Body content</p></body></html>
        """
        result = await meta_strategy.extract(html, "https://example.com")
        assert result is not None
        assert "OG Test Article" in result.content
        assert "An OG description" in result.content
        assert result.structured_data is not None
        assert result.structured_data["og:title"] == "OG Test Article"

    async def test_twitter_cards(self, meta_strategy: MetaExtractStrategy):
        html = """
        <html><head>
        <meta name="twitter:card" content="summary_large_image">
        <meta name="twitter:site" content="@test">
        <meta name="twitter:creator" content="@author">
        </head></html>
        """
        result = await meta_strategy.extract(html, "https://example.com")
        assert result is not None
        assert "summary_large_image" in result.content
        assert "@test" in result.content

    async def test_no_meta_returns_none(self, meta_strategy: MetaExtractStrategy):
        html = "<html><body><p>No meta here</p></body></html>"
        result = await meta_strategy.extract(html, "https://example.com")
        assert result is None

    async def test_title_only(self, meta_strategy: MetaExtractStrategy):
        html = "<html><head><title>Just a title</title></head><body></body></html>"
        result = await meta_strategy.extract(html, "https://example.com")
        assert result is not None
        assert "Just a title" in result.content
