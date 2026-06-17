from __future__ import annotations

import logging
from dataclasses import dataclass

from webgateway.config import ExtractorConfig, PostProcessingConfig
from webgateway.injection.detector import InjectionDetector
from webgateway.injection.types import InjectionDetectionResult
from webgateway.post_processing.cleaners import clean_markdown
from webgateway.post_processing.converters import convert_to_markdown
from webgateway.post_processing.dedup import DedupStore
from webgateway.post_processing.extractors import extract_main_content

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


class PostProcessingPipeline:
    """5-stage content post-processing pipeline."""

    def __init__(
        self,
        config: PostProcessingConfig,
        dedup_store: DedupStore | None = None,
        injection_detector: InjectionDetector | None = None,
    ) -> None:
        self._config = config
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
        skip_injection: bool = False,
    ) -> PostProcessingResult:
        """Run the full pipeline on *content*."""
        raw_len = len(content)
        pcfg = self._get_provider_config(provider or "")

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
        )
