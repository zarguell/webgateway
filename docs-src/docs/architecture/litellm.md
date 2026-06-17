# LiteLLM Integration Boundary

WebGateway is designed to sit _behind_ LiteLLM in an AI agent stack. The integration boundary is well-defined:

## LiteLLM's Role

- LLM routing and fallback between model providers (OpenAI, Anthropic, Google, etc.)
- API key management for end users
- Request/response logging for LLM calls
- Spend tracking and rate limiting per user

## WebGateway's Role

- Web search and content extraction abstraction
- Provider routing based on content policies (not LLM routing)
- DLP scanning for PII and secrets
- Session management for authenticated browsing
- Caching of web content (not LLM responses)

## Integration Points

LiteLLM calls WebGateway via HTTP or MCP:

```
Agent → LiteLLM (LLM calls) → WebGateway (web tools)
```

Or directly:

```
Agent → WebGateway (search, extract endpoints)
```

WebGateway never makes LLM calls (except the optional LLM Judge for ambiguous routing decisions).
