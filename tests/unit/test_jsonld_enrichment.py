from __future__ import annotations

import pytest

from serp_llm.config import PostProcessingConfig
from serp_llm.post_processing.pipeline import PostProcessingPipeline
from serp_llm.post_processing.strategies import StrategyResult


class _FakeStrategySelector:
    def __init__(self, structured_data: dict | None):
        self.structured_data = structured_data

    async def run(self, html: str, url: str, policy_matched: str | None):
        if self.structured_data and policy_matched:
            return StrategyResult(
                content="",
                format="markdown",
                structured_data=self.structured_data,
            )
        return None


def _make_pipeline(
    structured_data: dict | None = None,
    policy_matched: str | None = "test_policy",
) -> PostProcessingPipeline:
    config = PostProcessingConfig()
    selector = _FakeStrategySelector(structured_data)
    return PostProcessingPipeline(
        config=config, strategy_selector=selector,
    )


@pytest.mark.asyncio
async def test_jsonld_enriches_thin_content():
    html = (
        "<title>The Avengers (2012) - Rotten Tomatoes</title>"
        "<html><body>"
        "<nav>Skip to Main Content | Movies | TV Shows</nav>"
        "<p>Certified fresh picks and new releases tonight.</p>"
        "</body></html>"
    )
    json_ld = {
        "@type": "Movie",
        "name": "The Avengers",
        "description": "Earth's mightiest heroes must come together.",
        "datePublished": "2012-05-04",
        "aggregateRating": {"ratingValue": "4.2", "reviewCount": "350"},
    }
    pipeline = _make_pipeline(structured_data=json_ld)
    result = await pipeline.run(
        html, "https://rottentomatoes.com", format="html", policy_matched="test_policy",
    )
    assert "Earth's mightiest heroes" in result.content
    assert "The Avengers" in result.content
    assert result.extraction_fallback is True


@pytest.mark.asyncio
async def test_jsonld_skipped_when_content_has_title():
    html = (
        "<title>The Avengers - Review</title>"
        "<html><body>"
        "<h1>The Avengers</h1>"
        "<p>The Avengers is a 2012 superhero film directed by Joss Whedon. "
        "It features an ensemble cast including Robert Downey Jr., Chris Evans, "
        "Scarlett Johansson, and Chris Hemsworth. The film was a massive commercial "
        "success and received positive reviews from critics worldwide.</p>"
        "</body></html>"
    )
    json_ld = {
        "@type": "Movie",
        "name": "The Avengers",
        "description": "Earth's mightiest heroes must come together.",
    }
    pipeline = _make_pipeline(structured_data=json_ld)
    result = await pipeline.run(
        html, "https://example.com", format="html", policy_matched="test_policy",
    )
    assert "Earth's mightiest heroes" not in result.content
    assert "The Avengers is a 2012" in result.content


@pytest.mark.asyncio
async def test_jsonld_skipped_when_no_structured_data():
    html = (
        "<title>Some Movie</title>"
        "<html><body><nav>Navigation links here</nav></body></html>"
    )
    pipeline = _make_pipeline(structured_data=None)
    result = await pipeline.run(
        html, "https://example.com", format="html", policy_matched="test_policy",
    )
    assert "---" not in result.content


@pytest.mark.asyncio
async def test_jsonld_skipped_for_non_html_format():
    html = (
        "<title>The Avengers</title>"
        "<html><body><nav>Nav links</nav></body></html>"
    )
    json_ld = {
        "@type": "Movie",
        "name": "The Avengers",
        "description": "A superhero film.",
    }
    pipeline = _make_pipeline(structured_data=json_ld)
    result = await pipeline.run(
        html, "https://example.com", format="markdown", policy_matched="test_policy",
    )
    assert "A superhero film" not in result.content
