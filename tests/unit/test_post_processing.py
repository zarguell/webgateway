from __future__ import annotations

import tempfile

import pytest

from webgateway.post_processing.cleaners import clean_markdown
from webgateway.post_processing.converters import convert_to_markdown
from webgateway.post_processing.dedup import DedupStore
from webgateway.post_processing.extractors import (
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
