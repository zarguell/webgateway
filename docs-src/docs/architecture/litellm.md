# LiteLLM Integration Boundary

serpLLM is designed to sit _behind_ LiteLLM in an AI agent stack. The integration boundary is well-defined:

## LiteLLM's Role

- LLM routing and fallback between model providers (OpenAI, Anthropic, Google, etc.)
- API key management for end users
- Request/response logging for LLM calls
- Spend tracking and rate limiting per user

## serpLLM's Role

- Web search and content extraction abstraction
- Provider routing based on content policies (not LLM routing)
- DLP scanning for PII and secrets
- Session management for authenticated browsing
- Caching of web content (not LLM responses)

## Integration Points

LiteLLM calls serpLLM via HTTP or MCP:

```
Agent → LiteLLM (LLM calls) → serpLLM (web tools)
```

Or directly:

```
Agent → serpLLM (search, extract endpoints)
```

serpLLM never makes LLM calls (except the optional LLM Judge for ambiguous routing decisions).
