# Interactive API Docs

serpLLM provides auto-generated OpenAPI 3.1 documentation via FastAPI.

## Endpoints

| URL | Description |
|-----|-------------|
| `/api/docs` | Swagger UI — interactive, try-it-out enabled |
| `/api/redoc` | ReDoc UI — cleaner read-only reference |
| `/api/openapi.json` | Raw OpenAPI 3.1 JSON spec |
| `/mcp/schema` | MCP tool definitions |

## Authentication in Swagger UI

Click the **Authorize** button and paste your API key. All try-it-out requests will include the `Authorization: Bearer` header automatically.

## MCP Schema

The MCP schema describes the `web_search` and `web_extract` tools available through the MCP protocol. These are the same operations exposed by the REST API, formatted for AI agent consumption.
