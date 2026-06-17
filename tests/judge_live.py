"""Live test of the LLM Judge against real LM Studio.

Run directly:

    source .venv/bin/activate && python tests/judge_live.py
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import httpx

# ---------------------------------------------------------------------------
# Verify LM Studio is up
# ---------------------------------------------------------------------------

LM_STUDIO_URL = "http://127.0.0.1:1234/v1"
JUDGE_MODEL = "google/gemma-4-e2b"

try:
    resp = httpx.get(f"{LM_STUDIO_URL}/models", timeout=5)
    models = resp.json().get("data", [])
    model_ids = [m["id"] for m in models]
    assert JUDGE_MODEL in model_ids, f"{JUDGE_MODEL} not loaded. Available: {model_ids}"
    print(f"[OK] LM Studio reachable, {JUDGE_MODEL} loaded")
except Exception as exc:
    print(f"[FAIL] Cannot reach LM Studio: {exc}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Build a real LLMJudge
# ---------------------------------------------------------------------------

from webgateway.config import LLMJudgeConfig  # noqa: E402
from webgateway.judge import FailedProvider, LLMJudge  # noqa: E402

judge_cfg = LLMJudgeConfig(
    enabled=True,
    model=JUDGE_MODEL,
    base_url=LM_STUDIO_URL,
    api_key="lm-studio",
    trigger_on_policy_miss=True,
    trigger_on_retry=True,
    trigger_on_error_class=["403", "429", "bot_detected", "timeout"],
    confidence_threshold=0.50,
    cache_decisions=False,
    timeout=180,
    temperature=0.0,
)

config = SimpleNamespace(
    llm_judge=judge_cfg,
    providers={
        "searxng": SimpleNamespace(specialization="general"),
        "jina": SimpleNamespace(specialization="general"),
        "firecrawl": SimpleNamespace(specialization="stealth_primary"),
        "brave": SimpleNamespace(specialization="general"),
        "tavily": SimpleNamespace(specialization="agentic"),
        "exa": SimpleNamespace(specialization="semantic"),
    },
)
cm = SimpleNamespace(config=config)


class FakeRegistry:
    def list_names(self):
        return list(config.providers.keys())

    def has(self, name):
        return name in config.providers

    def list_metadata(self):
        return []


judge = LLMJudge(cm, FakeRegistry())


def show(label: str, decision):
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    if decision is None:
        print("  RESULT: None (judge failed open)")
        return
    print(f"  provider:          {decision.provider}")
    print(f"  fallback_chain:    {decision.fallback_chain}")
    print(f"  judge_invoked:     {decision.judge_invoked}")
    print(f"  judge_reasoning:   {decision.judge_reasoning_tag}")
    print(f"  policy_matched:    {decision.policy_matched}")


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

async def main():
    # --- on_policy_miss: search query ---
    d = await judge.evaluate_policy_miss(
        content_type="search",
        url=None,
        query="best practices for python async web scraping",
    )
    show("SEARCH QUERY: 'best practices for python async web scraping'", d)

    # --- on_policy_miss: JS-heavy SPA ---
    d = await judge.evaluate_policy_miss(
        content_type="extract",
        url="https://app.example.com/dashboard",
        query=None,
    )
    show("EXTRACT SPA: https://app.example.com/dashboard", d)

    # --- on_policy_miss: docs URL ---
    d = await judge.evaluate_policy_miss(
        content_type="extract",
        url="https://docs.python.org/3/library/asyncio.html",
        query=None,
    )
    show("EXTRACT DOCS: https://docs.python.org/3/library/asyncio.html", d)

    # --- on_policy_miss: news article ---
    d = await judge.evaluate_policy_miss(
        content_type="extract",
        url="https://www.bbc.com/news/world-us-canada-68000000",
        query=None,
    )
    show("EXTRACT NEWS: BBC article", d)

    # --- on_retry: 403 from jina ---
    d = await judge.evaluate_for_retry(
        content_type="extract",
        url="https://example.com/protected-page",
        query=None,
        failed_providers=[
            FailedProvider(
                name="jina",
                error_class="403",
                message="HTTP 403: Forbidden",
            ),
        ],
    )
    show("RETRY after 403 (jina failed)", d)

    # --- on_retry: timeout from searxng ---
    d = await judge.evaluate_for_retry(
        content_type="search",
        url=None,
        query="latest AI news",
        failed_providers=[
            FailedProvider(
                name="searxng",
                error_class="timeout",
                message="Request timed out after 30s",
            ),
        ],
    )
    show("RETRY after timeout (searxng failed)", d)

    # --- on_retry: multiple failures ---
    d = await judge.evaluate_for_retry(
        content_type="extract",
        url="https://example.com/article",
        query=None,
        failed_providers=[
            FailedProvider(name="jina", error_class="403", message="Forbidden"),
            FailedProvider(name="searxng", error_class="timeout", message="Timed out"),
        ],
    )
    show("RETRY after multiple failures (jina 403, searxng timeout)", d)

    print("\n" + "=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
