# Stealth Browsers

## Invisible Playwright

A C++-patched Firefox 150 with undetectable fingerprint. Designed for hard targets that block standard headless browsers.

```yaml
providers:
  invisible_playwright:
    base_url: http://invisible-playwright:3001
    stealth: true
    engine: firefox
    firefox_version: "150"
    cost_units_per_call: 0.8
    specialization: stealth_primary
```

Start with:

```bash
docker compose --profile stealth up -d
```

## Camoufox (Future)

Camoufox integration is planned as a second stealth browser option with different fingerprint characteristics.
