#!/usr/bin/env python3
"""Generate provider data policy pages from ProviderMetadata at build time.

Reads provider metadata from the gateway's provider adapters and generates
MkDocs-compatible markdown pages documenting each provider's data policies,
GDPR compliance, data residency, and privacy policy links.

Run during Docker build before `mkdocs build`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# Static provider metadata (mirrors ProviderMetadata from providers/base.py)
# In a full build, this would import the actual adapters. For now, we maintain
# a static mapping that stays in sync with the provider adapters.
PROVIDER_POLICIES = {
    "searxng": {
        "name": "SearXNG",
        "self_hosted": True,
        "data_retention_days": None,
        "trains_on_queries": False,
        "gdpr_compliant": True,
        "data_residency": ["self-hosted"],
        "privacy_policy_url": "https://docs.searxng.org/",
        "description": "Self-hosted meta-search engine. No data leaves your infrastructure.",
    },
    "jina": {
        "name": "Jina Reader",
        "self_hosted": False,
        "data_retention_days": 30,
        "trains_on_queries": False,
        "gdpr_compliant": True,
        "data_residency": ["EU", "US"],
        "privacy_policy_url": "https://jina.ai/legal/privacy-policy",
        "description": "Lightweight read-it-later extraction API with generous free tier.",
    },
    "brave": {
        "name": "Brave Search",
        "self_hosted": False,
        "data_retention_days": 30,
        "trains_on_queries": False,
        "gdpr_compliant": True,
        "data_residency": ["US", "CA"],
        "privacy_policy_url": "https://brave.com/privacy/search/",
        "description": "Fast, privacy-respecting search API.",
    },
    "tavily": {
        "name": "Tavily",
        "self_hosted": False,
        "data_retention_days": 30,
        "trains_on_queries": True,
        "gdpr_compliant": True,
        "data_residency": ["US"],
        "privacy_policy_url": "https://tavily.com/privacy",
        "description": "AI-optimized search API for RAG and agent workloads.",
    },
    "firecrawl": {
        "name": "Firecrawl",
        "self_hosted": False,
        "data_retention_days": 30,
        "trains_on_queries": False,
        "gdpr_compliant": True,
        "data_residency": ["US", "EU"],
        "privacy_policy_url": "https://www.firecrawl.dev/privacy",
        "description": "Full-featured extraction with JavaScript rendering.",
    },
    "firecrawl_selfhosted": {
        "name": "Firecrawl (Self-Hosted)",
        "self_hosted": True,
        "data_retention_days": None,
        "trains_on_queries": False,
        "gdpr_compliant": True,
        "data_residency": ["self-hosted"],
        "privacy_policy_url": "https://github.com/nicklausw/firecrawl",
        "description": "Self-hosted Firecrawl instance. Full data control.",
    },
    "invisible_playwright": {
        "name": "Invisible Playwright",
        "self_hosted": True,
        "data_retention_days": None,
        "trains_on_queries": False,
        "gdpr_compliant": True,
        "data_residency": ["self-hosted"],
        "privacy_policy_url": None,
        "description": "C++-patched Firefox 150 stealth browser for undetectable extraction.",
    },
}


def generate_provider_page(provider_id: str, policy: dict) -> str:
    """Generate a MkDocs markdown page for a single provider."""
    lines = [
        f"# {policy['name']} — Data Policy",
        "",
        policy["description"],
        "",
        "## Data Handling",
        "",
        f"- **Self-Hosted:** {'Yes' if policy['self_hosted'] else 'No'}",
        f"- **Data Retention:** {f'{policy["data_retention_days"]} days' if policy['data_retention_days'] else 'Not applicable (self-hosted)'}",
        f"- **Trains on Queries:** {'Yes' if policy['trains_on_queries'] else 'No'}",
        f"- **GDPR Compliant:** {'Yes' if policy['gdpr_compliant'] else 'No'}",
        f"- **Data Residency:** {', '.join(policy['data_residency'])}",
        "",
        "## Privacy Policy",
        "",
    ]

    if policy["privacy_policy_url"]:
        lines.append(f"[{policy['privacy_policy_url']}]({policy['privacy_policy_url']})")
    else:
        lines.append("No public privacy policy URL available (self-hosted component).")

    lines.extend([
        "",
        "---",
        "",
        "*This page is auto-generated. Always verify current policies on the provider's website.*",
        "",
    ])

    return "\n".join(lines)


def main(output_dir: str = "docs-src/docs/providers/policies") -> None:
    """Generate all provider data policy pages."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    index_lines = [
        "# Provider Data Policies",
        "",
        "The following pages document each provider's data handling, privacy, and compliance characteristics.",
        "",
        "| Provider | Self-Hosted | GDPR | Trains on Queries | Data Residency |",
        "|----------|-------------|------|-------------------|----------------|",
    ]

    for provider_id in sorted(PROVIDER_POLICIES.keys()):
        policy = PROVIDER_POLICIES[provider_id]
        # Generate individual page
        page_content = generate_provider_page(provider_id, policy)
        page_path = out_path / f"{provider_id}.md"
        page_path.write_text(page_content)
        print(f"Generated: {page_path}")

        # Add to index table
        index_lines.append(
            f"| [{policy['name']}]({provider_id}.md) | "
            f"{'Yes' if policy['self_hosted'] else 'No'} | "
            f"{'Yes' if policy['gdpr_compliant'] else 'No'} | "
            f"{'Yes' if policy['trains_on_queries'] else 'No'} | "
            f"{', '.join(policy['data_residency'])} |"
        )

    index_lines.extend([
        "",
        "---",
        "",
        "*This page is auto-generated. Always verify current policies on each provider's website.*",
    ])

    index_path = out_path / "index.md"
    index_path.write_text("\n".join(index_lines))
    print(f"Generated: {index_path}")

    # Generate JSON manifest for reference
    manifest = {k: {kk: vv for kk, vv in v.items() if kk != "description"}
                for k, v in PROVIDER_POLICIES.items()}
    manifest_path = out_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Generated: {manifest_path}")


if __name__ == "__main__":
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "docs-src/docs/providers/policies"
    main(output_dir)
