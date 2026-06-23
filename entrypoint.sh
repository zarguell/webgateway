#!/bin/sh
set -e

# Ensure volume-mounted directories are writable by the non-root user.
# Docker bind mounts inherit host ownership, which may not match
# the container UID. Fix ownership only if the directory already exists
# and is not writable (bind mount scenario).
for dir in /app/data /app/logs /app/sessions; do
    if [ -d "$dir" ] && [ ! -w "$dir" ]; then
        chown "$(id -u):$(id -g)" "$dir" 2>/dev/null || true
    fi
done

exec "$@"
