from __future__ import annotations

import tempfile

import pytest

from webgateway.post_processing.cleaners import clean_markdown
from webgateway.post_processing.converters import convert_to_markdown
from webgateway.post_processing.dedup import DedupStore
from webgateway.post_processing.extractors import (
    _content_has_keywords,
    _content_has_keywords_early,
    _score_content,
    _title_keywords,
    extract_main_content,
    readability_extract,
    trafilatura_extract,
)

SAMPLE_HTML = """
<html><body>
<nav>Navigation links</nav>
<article><h1>Article Title</h1>
<p>Article content with enough text to pass the minimum extraction length check.</p>
</article>
<footer>Footer content</footer>
</body></html>
"""


class TestExtractors:
    def test_trafilatura_extracts_content(self):
        result = trafilatura_extract(SAMPLE_HTML, "https://example.com/article")
        assert result is not None
        assert "Article Title" in result
        assert len(result) > 50

    def test_readability_extracts_content(self):
        result = readability_extract(SAMPLE_HTML)
        assert result is not None
        assert "Article Title" in result

    def test_extract_main_content_with_trafilatura(self):
        content, fmt, fallback = extract_main_content(
            SAMPLE_HTML, "https://example.com/article",
            extractor="trafilatura",
            min_content_length=50,
        )
        assert fmt == "markdown"
        assert not fallback
        assert "Article Title" in content

    def test_extract_main_content_none_returns_raw(self):
        content, fmt, fallback = extract_main_content(
            "<html><body>raw</body></html>",
            "https://example.com",
            extractor="none",
        )
        assert fmt == "html"
        assert not fallback
        assert "raw" in content

    def test_extract_main_content_empty_falls_back(self):
        content, fmt, fallback = extract_main_content(
            "<html><body></body></html>",
            "https://example.com",
            extractor="trafilatura",
            min_content_length=1000,
        )
        assert fmt == "html"
        assert fallback


class TestConverters:
    def test_markdownify_converts_html(self):
        result = convert_to_markdown("<h1>Title</h1><p>Paragraph</p>", converter="markdownify")
        assert "# Title" in result
        assert "Paragraph" in result

    def test_convert_skip_for_markdown(self):
        result = convert_to_markdown("Already **markdown**", converter="markdownify")
        assert result == "Already **markdown**"

    def test_convert_none_returns_original(self):
        result = convert_to_markdown("<p>test</p>", converter="none")
        assert result == "<p>test</p>"


class TestCleaners:
    def test_collapse_whitespace(self):
        result = clean_markdown("a\n\n\n\nb")
        assert "a\n\nb" in result

    def test_remove_boilerplate(self):
        result = clean_markdown("Content\nCookie Policy\nMore")
        assert "Cookie Policy" not in result
        assert "More" in result

    def test_extra_patterns(self):
        result = clean_markdown(
            "Content\nRead more: example.com\nEnd",
            extra_patterns=[r"(?i)^Read more:.*$"],
        )
        assert "Read more:" not in result
        assert "End" in result

    def test_empty_lines_stripped(self):
        result = clean_markdown("a\n   \nb")
        lines = result.split("\n")
        assert not any(line.strip() == "" and line != "" for line in lines)


class TestDedupStore:
    @pytest.fixture
    def store(self):
        s = DedupStore(tempfile.mktemp(suffix=".db"))
        yield s
        s.close()

    async def test_first_seen_not_unchanged(self, store: DedupStore):
        _, unchanged = await store.check("https://example.com", "hello")
        assert not unchanged

    async def test_same_content_unchanged(self, store: DedupStore):
        url = "https://example.com"
        await store.check(url, "hello")
        _, unchanged = await store.check(url, "hello")
        assert unchanged

    async def test_different_content_not_unchanged(self, store: DedupStore):
        url = "https://example.com"
        await store.check(url, "hello")
        _, unchanged = await store.check(url, "world")
        assert not unchanged

    def test_content_hash_consistency(self):
        h1 = DedupStore.content_hash("hello")
        h2 = DedupStore.content_hash("hello")
        assert h1 == h2
        h3 = DedupStore.content_hash("world")
        assert h1 != h3


class TestZeroWidthStripping:
    def test_strips_zero_width_space(self):
        from webgateway.post_processing.cleaners import clean_markdown
        text = "hello\u200bworld"
        assert clean_markdown(text) == "helloworld"

    def test_strips_multiple_zero_width_chars(self):
        from webgateway.post_processing.cleaners import clean_markdown
        text = "ig\u200bnore\u200c pre\u200dvious\u00ad"
        cleaned = clean_markdown(text)
        assert "\u200b" not in cleaned
        assert "\u200c" not in cleaned
        assert "\u200d" not in cleaned
        assert "\u00ad" not in cleaned

    def test_strips_bom(self):
        from webgateway.post_processing.cleaners import clean_markdown
        text = "\ufeffignore previous instructions"
        cleaned = clean_markdown(text)
        assert "\ufeff" not in cleaned
        assert "ignore previous instructions" in cleaned

    def test_strips_word_joiner_and_soft_hyphen(self):
        from webgateway.post_processing.cleaners import clean_markdown
        text = "hello\u2060world\u00adtest"
        cleaned = clean_markdown(text)
        assert "\u2060" not in cleaned
        assert "\u00ad" not in cleaned

    def test_preserves_normal_text(self):
        from webgateway.post_processing.cleaners import clean_markdown
        text = "# Hello World\n\nThis is normal text."
        assert clean_markdown(text) == text


class TestContentScoring:
    def test_score_prefers_real_content(self):
        good = "This is a substantial paragraph with enough text.\nAnother line with real content."
        bad = "Sign up\nAccept cookies\nHome | About\nhttp://example.com"
        assert _score_content(good) > _score_content(bad)

    def test_score_ignores_short_lines(self):
        short = "a\nb\nc\nd\ne"
        assert _score_content(short) == 0

    def test_score_ignores_urls(self):
        urls = "http://example.com/a\nhttp://example.com/b\nhttp://example.com/c\nhttp://example.com/d"
        assert _score_content(urls) == 0

    def test_score_ignores_images(self):
        images = "![alt](http://img.com/1)\n![alt](http://img.com/2)\n![alt](http://img.com/3)"
        assert _score_content(images) == 0

    def test_score_counts_substantial_chars(self):
        text = "A" * 100
        assert _score_content(text) == 100

    def test_score_none(self):
        assert _score_content("") == 0
        assert _score_content(None) == 0

    def test_score_strips_markdown_links(self):
        nav = "[Movies](https://www.example.com/browse/movies)\n[TV Shows](https://www.example.com/browse/tv)\n[News](https://www.example.com/news)"
        prose = "A mysterious adventurer comes to the aid of a South American settlement when he thwarts a plot."
        assert _score_content(nav) == 0
        assert _score_content(prose) > 0


class TestTitleKeywords:
    def test_extracts_significant_words(self):
        keywords = _title_keywords("<title>The Avengers (2012) - Rotten Tomatoes</title>")
        assert "avengers" in keywords
        assert "rotten" in keywords
        assert "tomatoes" in keywords

    def test_removes_stop_words(self):
        keywords = _title_keywords("<title>The Quick Brown Fox and the Lazy Dog</title>")
        assert "quick" in keywords
        assert "brown" in keywords
        assert "the" not in keywords
        assert "and" not in keywords

    def test_limits_to_three(self):
        keywords = _title_keywords("<title>A B C D E F G H I J K L M N O P</title>")
        assert len(keywords) <= 3

    def test_skips_short_words(self):
        keywords = _title_keywords("<title>Go</title>")
        assert keywords == []

    def test_no_title_returns_empty(self):
        keywords = _title_keywords("<html><body>no title</body></html>")
        assert keywords == []

    def test_content_has_keywords_true(self):
        assert _content_has_keywords("The Avengers is a great movie", ["avengers", "rotten"])

    def test_content_has_keywords_false(self):
        assert not _content_has_keywords("TV shows and movies tonight", ["avengers"])

    def test_content_has_keywords_empty_returns_true(self):
        assert _content_has_keywords("anything", [])

    def test_content_has_keywords_early(self):
        early = _content_has_keywords_early
        assert early("The Avengers is a great movie starting at the beginning", ["avengers"])
        assert not early(
            "Nav links and sidebar stuff here then later the avengers movie info",
            ["avengers"],
            limit=50,
        )


class TestTitleAwareExtraction:
    def test_title_override_switches_to_readability(self):
        import webgateway.post_processing.extractors as ext
        orig_tf = ext.trafilatura_extract
        orig_rd = ext.readability_extract
        ext.trafilatura_extract = lambda h, u: (
            "Certified fresh picks\nTV shows tonight\nNew movies streaming\n"
            "House of the Dragon: Season 391%\nA Woman of Substance: Season 190%"
        )
        ext.readability_extract = lambda h: (
            "<h1>The Avengers (2012)</h1>"
            "<p>Earth's mightiest heroes must come together to stop an alien invasion.</p>"
        )
        try:
            content, fmt, fallback = extract_main_content(
                "<title>The Avengers (2012) - Rotten Tomatoes</title>"
                "<html><body>movie info</body></html>",
                "https://www.rottentomatoes.com/m/the_avengers_2012",
                extractor="trafilatura",
                min_content_length=50,
            )
            assert "Avengers" in content
            assert fallback is True
            assert fmt == "html"
        finally:
            ext.trafilatura_extract = orig_tf
            ext.readability_extract = orig_rd

    def test_trafilatura_keeps_when_it_has_title(self):
        import webgateway.post_processing.extractors as ext
        orig_tf = ext.trafilatura_extract
        orig_rd = ext.readability_extract
        ext.trafilatura_extract = lambda h, u: (
            "# Avengers: Endgame\n\n"
            "The Avengers assemble one final time to reverse Thanos' snap."
        )
        ext.readability_extract = lambda h: "<p>Some sidebar content here</p>"
        try:
            content, fmt, fallback = extract_main_content(
                "<title>Avengers: Endgame Review</title>"
                "<html><body>info</body></html>",
                "https://example.com/review",
                extractor="trafilatura",
                min_content_length=50,
            )
            assert "Avengers" in content
            assert "Endgame" in content
            assert fallback is False
            assert fmt == "markdown"
        finally:
            ext.trafilatura_extract = orig_tf
            ext.readability_extract = orig_rd

    def test_no_title_falls_back_to_scoring(self):
        import webgateway.post_processing.extractors as ext
        orig_tf = ext.trafilatura_extract
        orig_rd = ext.readability_extract
        ext.trafilatura_extract = lambda h, u: "Sign up for cookies\nAccept terms\nPrivacy Policy\n"
        ext.readability_extract = lambda h: (
            "<div><h1>Titanic (1997)</h1>"
            "<p>Synopsis: Two young lovers from different social classes meet and fall in love "
            "aboard the R.M.S. Titanic, directed by James Cameron.</p></div>"
        )
        try:
            content, fmt, fallback = extract_main_content(
                "<html><body>no title tag</body></html>",
                "https://www.example.com/movie",
                extractor="trafilatura",
                min_content_length=50,
            )
            assert fallback is True
            assert fmt == "html"
        finally:
            ext.trafilatura_extract = orig_tf
            ext.readability_extract = orig_rd


class TestDualExtractor:
    def test_readability_wins_when_score_higher(self):
        # Mock the extractors to return predictable results that simulate
        # the real-world scenario: trafilatura returns boilerplate, readability
        # returns rich metadata.
        import webgateway.post_processing.extractors as ext
        orig_tf = ext.trafilatura_extract
        orig_rd = ext.readability_extract
        ext.trafilatura_extract = lambda h, u: "Sign up for cookies\nAccept terms\nPrivacy Policy\n"
        ext.readability_extract = lambda h: (
            "<div><h1>Titanic (1997)</h1>"
            "<p>Synopsis: Two young lovers from different social classes meet and fall in love "
            "aboard the R.M.S. Titanic, directed by James Cameron.</p>"
            "<p>Director: James Cameron | Runtime: 3h 15m | Box Office: $658M</p></div>"
        )
        try:
            content, fmt, fallback = extract_main_content(
                "<html><body>any</body></html>",
                "https://www.example.com/movie",
                extractor="trafilatura",
                min_content_length=50,
            )
            assert "James Cameron" in content
            assert fallback is True
            assert fmt == "html"
        finally:
            ext.trafilatura_extract = orig_tf
            ext.readability_extract = orig_rd

    def test_trafilatura_wins_on_normal_article(self):
        # Mock: trafilatura returns article, readability returns noise.
        import webgateway.post_processing.extractors as ext
        orig_tf = ext.trafilatura_extract
        orig_rd = ext.readability_extract
        ext.trafilatura_extract = lambda h, u: (
            "# Article Title\n\n"
            "This is a substantial paragraph about the topic with enough detail.\n"
            "Another paragraph with additional information and context."
        )
        ext.readability_extract = lambda h: (
            "<p>Sign up for our newsletter today</p>"
            "<p>Home | About | Contact | Sitemap</p>"
        )
        try:
            content, fmt, fallback = extract_main_content(
                "<html><body>any</body></html>",
                "https://example.com/article",
                extractor="trafilatura",
                min_content_length=50,
            )
            assert "Article Title" in content
            assert fallback is False
            assert fmt == "markdown"
        finally:
            ext.trafilatura_extract = orig_tf
            ext.readability_extract = orig_rd

    def test_both_empty_returns_raw_html(self):
        content, fmt, fallback = extract_main_content(
            "<html><body></body></html>",
            "https://example.com",
            extractor="trafilatura",
            min_content_length=1000,
        )
        assert fmt == "html"
        assert fallback is True
