#!/usr/bin/env bash
# Ensure the local serpLLM stack is running.
# Starts it if down, waits for health, then exits.
#
# Usage:
#   ./scripts/ensure-gateway.sh
#
# Agents should run this before using web_search / web_extract MCP tools.
set -euo pipefail

COMPOSE_FILE="docker-compose.local.yml"
HEALTH_URL="http://localhost:8080/health"
POLL_INTERVAL=2
MAX_ATTEMPTS=60  # 60 * 2s = 120s total (covers Crawl4AI first-time browser download)

# Check if the gateway is already healthy
if curl -sf "${HEALTH_URL}" > /dev/null 2>&1; then
    echo "serpLLM is already running and healthy"
    exit 0
fi

echo "serpLLM is not responding. Starting the local stack..."

# Start the stack (with --build to catch source code changes)
if ! docker compose -f "${COMPOSE_FILE}" --profile local up -d --build 2>&1; then
    echo "Error: Failed to start serpLLM stack" >&2
    exit 1
fi

# Wait for the gateway to become healthy
echo "Waiting for serpLLM to become healthy..."
for ((i = 1; i <= MAX_ATTEMPTS; i++)); do
    if curl -sf "${HEALTH_URL}" > /dev/null 2>&1; then
        echo "serpLLM is ready (after ~$((i * POLL_INTERVAL))s)"
        exit 0
    fi
    sleep "${POLL_INTERVAL}"
done

echo "Error: serpLLM did not become healthy within $((MAX_ATTEMPTS * POLL_INTERVAL)) seconds" >&2
echo "Check logs: docker compose -f ${COMPOSE_FILE} logs serpllm" >&2
exit 1
