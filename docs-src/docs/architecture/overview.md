# System Design Overview

## Architecture

```
Request → Auth → Policy Engine → DLP outbound → Cache lookup
  → Proxy resolve → Provider dispatch (with fallback) → DLP inbound
  → Cache write → Response
```

## Component Diagram

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  FastAPI App  │────▶│ Auth Module  │────▶│ Policy Engine│
│  (main.py)   │     │  (auth.py)   │     │  (policy/)   │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                    ┌─────────────────────────────┐
                    │                             │
              ┌─────▼──────┐              ┌──────▼──────┐
              │ DLP Scanner│              │Cache Lookup │
              │  (dlp/)    │              │ (cache/)    │
              └─────┬──────┘              └──────┬──────┘
                    │                             │
              ┌─────▼─────────────────────────────▼──────┐
              │         GatewayService (service.py)      │
              │  Retry logic, fallback chains, dispatch  │
              └─────┬─────────────────────────────┬──────┘
                    │                             │
              ┌─────▼──────┐              ┌──────▼──────┐
              │ProxyResolver│              │  Providers  │
              │ (proxy.py) │              │(providers/) │
              └────────────┘              └─────────────┘
```

## Data Flow

1. **Request** hits FastAPI route handler
2. **Auth** validates Bearer token against config keys, SQLite keys, or bootstrap key
3. **Policy Engine** matches request against YAML rules
4. **DLP Outbound** scans request for sensitive data
5. **Cache** looks up existing response (optional)
6. **Proxy** resolves the proxy route if policy specifies one
7. **Provider** dispatches to the selected adapter with fallback chain
8. **DLP Inbound** scans response for leaked secrets
9. **Cache** stores the response (optional)
10. **Response** returned to caller with audit log entry
