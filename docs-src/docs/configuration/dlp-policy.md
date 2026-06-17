# DLP Policy Configuration

Data Loss Prevention (DLP) policies protect sensitive data from being sent to or received from providers.

## Rule Types

| Action | Description |
|--------|-------------|
| `block` | Reject the request/response entirely |
| `redact` | Replace matched text with a placeholder |
| `reroute` | Send to a different provider (outbound only) |
| `log` | Record the match without modifying |

## Example

```yaml
dlp_policies:
  - name: no_pii_upstream
    description: "Block/redact PII before sending to cloud providers"
    enabled: true
    applies_to_providers:
      - tavily
      - brave
      - jina
    outbound_rules:
      - name: "US SSN"
        pattern: '\\b(?!000|666|9\\d{2})\\d{3}[-.\\s]?(?!00)\\d{2}[-.\\s]?(?!0000)\\d{4}\\b'
        action: block
        severity: critical
      - name: "Email address"
        pattern: '\\b[\\w.]+@[\\w.]+\\.[a-z]{2,}\\b'
        action: redact
        replacement: "[REDACTED_EMAIL]"
```

Patterns adapted from [Gitleaks](https://github.com/gitleaks/gitleaks) (MIT) and [secrets-patterns-db](https://github.com/mazen160/secrets-patterns-db) (CC BY 4.0).
