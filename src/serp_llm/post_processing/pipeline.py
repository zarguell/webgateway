from __future__ import annotations

import logging
from dataclasses import dataclass

from serp_llm.config import ExtractorConfig, PostProcessingConfig
from serp_llm.injection.detector import InjectionDetector
from serp_llm.injection.types import InjectionDetectionResult
from serp_llm.post_processing.cleaners import clean_markdown
from serp_llm.post_processing.converters import convert_to_markdown
from serp_llm.post_processing.dedup import DedupStore
from serp_llm.post_processing.extractors import (
    _content_has_keywords_early,
    _title_keywords,
    extract_main_content,
)
from serp_llm.post_processing.strategies import StrategySelector

logger = logging.getLogger(__name__)


@dataclass
class PostProcessingResult:
    """Result of running the pipeline on a provider response."""

    content: str
    format: str = "markdown"
    extractor_used: str | None = None
    extraction_fallback: bool = False
    content_length_raw: int = 0
    content_length_processed: int = 0
    reduction_pct: float = 0.0
    content_unchanged: bool = False
    content_hash: str | None = None
    injection: InjectionDetectionResult | None = None
    structured_data: dict | list | None = None


class PostProcessingPipeline:
    """5-stage content post-processing pipeline."""

    def __init__(
        self,
        config: PostProcessingConfig,
        *,
        strategy_selector: StrategySelector | None = None,
        dedup_store: DedupStore | None = None,
        injection_detector: InjectionDetector | None = None,
    ) -> None:
        self._config = config
        self._strategy_selector = strategy_selector
        self._dedup = dedup_store
        self._injection_detector = injection_detector

    def _get_provider_config(self, provider: str) -> ExtractorConfig:
        providers = getattr(self._config, "providers", {})
        if provider in providers:
            return providers[provider]
        return self._config.default

    async def run(
        self,
        content: str,
        url: str,
        *,
        format: str = "html",
        provider: str | None = None,
        policy_matched: str | None = None,
        skip_injection: bool = False,
    ) -> PostProcessingResult:
        """Run the full pipeline on *content*."""
        raw_len = len(content)
        pcfg = self._get_provider_config(provider or "")
        structured_data: dict | list | None = None

        # Stage 0: extraction strategy (policy-driven, JSON-LD etc.)
        # Runs on raw HTML to extract structured data. The full content still
        # flows through stages 1-2 so agents get both the extracted page and
        # any structured data we found.
        if self._strategy_selector is not None and format == "html":
            strategy_result = await self._strategy_selector.run(
                content, url, policy_matched=policy_matched,
            )
            if strategy_result is not None:
                structured_data = strategy_result.structured_data

        # Stage 1: Main content extraction
        extractor = pcfg.stage1_extractor
        if format == "html" and extractor != "none":
            extracted, _, used_fallback = extract_main_content(
                content,
                url,
                extractor=extractor,
                min_content_length=self._config.cleaning.min_content_length,
            )
        else:
            extracted, _, used_fallback = content, format, False

        # Stage 1.5: JSON-LD enrichment
        # When the extractor produced thin/missed content but we found rich
        # structured data, prepend the JSON-LD as readable markdown.
        content_is_thin = len(extracted) < self._config.cleaning.min_content_length
        title_missing = not _content_has_keywords_early(
            extracted, _title_keywords(content)
        )
        if (
            structured_data
            and isinstance(structured_data, dict)
            and format == "html"
            and (content_is_thin or title_missing)
        ):
            from serp_llm.post_processing.strategies.json_ld import (
                flatten_jsonld_to_markdown,
            )

            jsonld_md = flatten_jsonld_to_markdown(structured_data)
            if jsonld_md and len(jsonld_md) > 50:
                extracted = jsonld_md + "\n\n---\n\n" + extracted
                used_fallback = True

        # Stage 2: HTML -> Markdown conversion
        converter = pcfg.stage2_converter
        markdown = convert_to_markdown(extracted, converter=converter)

        # Stage 3: Markdown cleaning
        if pcfg.stage3_clean:
            markdown = clean_markdown(
                markdown,
                extra_patterns=self._config.cleaning.additional_boilerplate_patterns,
            )

        # Stage 4: Deduplication
        content_hash: str | None = None
        content_unchanged = False
        if pcfg.stage4_deduplicate and self._dedup is not None:
            content_hash, content_unchanged = await self._dedup.check(url, markdown)

        # Stage 5: Prompt injection detection
        injection_result: InjectionDetectionResult | None = None
        if (
            self._injection_detector is not None
            and not skip_injection
        ):
            injection_result = self._injection_detector.detect(markdown, url)
            if (
                injection_result.action == "scrub"
                and injection_result.scrubbed_content is not None
            ):
                markdown = injection_result.scrubbed_content

        processed_len = len(markdown)
        reduction_pct = (
            round((1 - processed_len / raw_len) * 100, 1)
            if raw_len > 0 else 0.0
        )

        return PostProcessingResult(
            content=markdown,
            format="markdown",
            extractor_used=extractor if extractor != "none" else None,
            extraction_fallback=used_fallback,
            content_length_raw=raw_len,
            content_length_processed=processed_len,
            reduction_pct=reduction_pct,
            content_unchanged=content_unchanged,
            content_hash=content_hash,
            injection=injection_result,
            structured_data=structured_data,
        )
