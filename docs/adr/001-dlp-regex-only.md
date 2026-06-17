# ADR-001: DLP Middleware — Pure Regex, No External Engine

**Date:** 2026-06-17  
**Status:** Accepted  
**Decision Maker:** Project team  
**Supersedes:** None  

---

## Context

The PRD (§4.6) specifies a DLP middleware with two enforcement points:

- **Outbound** (before provider dispatch): scan query text and URLs for sensitive data. Actions: `block`, `redact`, `reroute` (force self-hosted provider).
- **Inbound** (after provider response): scan returned content for secrets/PII. Actions: `redact_with` (replace match with placeholder), `block_response`.

The middleware sits in the request hot path — every search query and every extraction response passes through it. Performance and dependency weight are critical.

### Alternatives Considered

| Option | Description | Docker Image Impact | Key Trade-off |
|--------|-------------|---------------------|---------------|
| **Microsoft Presidio (Slim)** | Use `SlimSpacyNlpEngine` with `en_core_web_sm` (12MB model). 30+ built-in PII recognizers with checksum validation. | +250–350 MB (spaCy is a hard dependency) | Rich PII detection, but spaCy bloats the slim gateway image. No built-in API key / secret recognizers — would need custom ones anyway. |
| **Presidio (NlpEngineMock)** | Use Presidio with a no-op NLP engine (no model download). Pure regex recognizers only. | +200–300 MB (spaCy package still required) | Gets Presidio's recognizer framework without model weight, but we'd only use regex — which we can do with zero deps. |
| **scrubadub** | Python PII scrubber with locale-aware detectors. | +100 MB (scikit-learn, textblob, NLTK corpora) | Heavy ML deps for name detection we don't need. Dormant since Sept 2023. |
| **piiscrub / pii-redactor** | Zero-dep Python libraries covering PII + secrets. | ~0 | Right feature set but very early (3–4 stars, released 2026). Too early to depend on for production infrastructure. |
| **Pure Regex (chosen)** | Config-driven regex patterns compiled at startup. No external DLP dependency. | 0 | Full control, zero deps, sub-millisecond scanning. We curate patterns from well-established sources (Gitleaks MIT, secrets-patterns-db CC BY 4.0). |

## Decision

**Use pure regex with config-driven patterns.** No external DLP/PII library dependency.

### Rationale

1. **Slim gateway principle (PRD §3.3):** "Slim gateway image — no browsers, no heavy dependencies baked in." Adding 250–350 MB of spaCy for DLP we can handle with stdlib `re` violates this principle.

2. **Secrets > PII for this use case:** The primary risk is API keys/tokens leaking upstream to cloud providers (Brave, Tavily, Firecrawl). Presidio has **no built-in recognizers** for AWS keys, OpenAI keys, GitHub tokens, or Bearer tokens — we'd write custom regex recognizers regardless of which option we chose.

3. **Well-documented patterns:** Battle-tested regex patterns for all major secret types (AWS, OpenAI, Anthropic, GitHub, GCP, SSH keys, connection strings) are available from:
   - [Gitleaks](https://github.com/gitleaks/gitleaks) (MIT license, 170+ rules)
   - [secrets-patterns-db](https://github.com/mazen160/secrets-patterns-db) (CC BY 4.0, 1600+ patterns)
   - [TruffleHog](https://github.com/trufflesecurity/trufflehog) (AGPL, 800+ detectors)

4. **Performance:** Pure regex is sub-millisecond per pattern. The middleware is in the request hot path — every millisecond matters.

5. **Config-driven (PRD §3.4):** "Policy-as-config — all routing behavior is driven by a hot-reloadable `config.yaml`." Regex patterns in config YAML fits this principle perfectly. Users add/remove patterns without code changes.

6. **Upgrade path preserved:** If NLP-based PII detection (names, addresses) becomes necessary later, we can add Presidio as an **optional** install behind a feature flag — `dlp.engine: regex | presidio`. The scanner interface remains the same.

### Pattern Sources & Licensing

Default patterns bundled with the gateway are adapted from:
- **Gitleaks** (`config/gitleaks.toml`) — MIT License
- **secrets-patterns-db** — CC BY 4.0

Attribution is included in the `config.yaml` comments.

## Consequences

- **We maintain the pattern set ourselves.** This is low-burden: secret formats (AWS `AKIA...`, OpenAI `sk-...`, GitHub `ghp_...`) are stable and change rarely.
- **No NLP-based entity detection.** The gateway cannot detect person names, street addresses, or other context-dependent PII. This is acceptable for v1 — the gateway is infrastructure middleware, not a compliance product.
- **Credit card validation uses Luhn checksum** implemented in ~15 lines of Python (no dependency).
- **False positive management** is handled via config: users can disable patterns, add allowlists, or tighten regex specificity.

## Future Considerations

- If the gateway needs to detect names/addresses (e.g., for HIPAA use cases), add Presidio as an optional dependency behind `dlp.engine: presidio` config flag.
- If pattern count grows significantly (>50), consider pre-filtering with keyword indices (like Gitleaks does) to avoid running all patterns on every request.
- Consider adding entropy-based detection (Shannon entropy > 3.5) for high-entropy strings that don't match known patterns — useful for catching unknown secret formats.
