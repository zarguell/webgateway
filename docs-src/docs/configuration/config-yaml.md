# config.yaml Reference

The `config.yaml` file defines all routing behavior, provider configuration, and operational parameters. It is safe to commit to version control — secrets live in `.env`.

## Structure

```yaml
# Top-level keys
defaults:         # Default behavior when no policy matches
policies:         # Routing rules (evaluated in order, first match wins)
proxies:          # Named proxy definitions
providers:        # Provider adapter configuration
llm_judge:        # LLM-based routing fallback
dlp_policies:     # Data loss prevention rules
auth:             # Static API keys (legacy, prefer SQLite-backed)
logging:          # Audit log configuration
sessions:         # Cookie jar / session store
stealth:          # Stealth browser settings
cache:            # Response cache configuration
circuit_breaker:  # Per-provider failure thresholds
quotas:           # Usage limits per provider
alerts:           # Webhook alerting
mcp:              # MCP server settings
post_processing:  # Content cleaning pipeline
```

## Defaults

```yaml
defaults:
  search_provider: searxng
  extract_provider: jina
  timeout: 15
  retry:
    strategy: fallback
    max_attempts: 3
    fallback_chain:
      - jina
      - firecrawl
      - playwright
```

## Providers

```yaml
providers:
  searxng:
    base_url: http://searxng:8080
  jina:
    api_key: ${JINA_API_KEY}
  brave:
    api_key: ${BRAVE_API_KEY}
```

Provider config values support `${ENV_VAR}` and `${ENV_VAR:-default}` syntax.
