# Proxy Configuration

Named proxies can be referenced in policy rules and provider configs.

## HTTP Proxy

```yaml
proxies:
  gluetun:
    type: http
    url: http://gluetun:8888
  residential_us:
    type: http
    url: http://brightdata-proxy:24000
```

## SOCKS5 Proxy

```yaml
proxies:
  tor:
    type: socks5
    url: socks5://tor:9050
```

## Gluetun Setup

The [Gluetun](https://github.com/qdm12/gluetun) VPN client runs as a sidecar with HTTP proxy support:

```bash
docker compose --profile vpn up -d
```

Set your WireGuard keys in `.env`:

```env
WIREGUARD_PRIVATE_KEY=
WIREGUARD_ADDRESSES=
```
