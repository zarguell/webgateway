from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from webgateway.sessions.models import (
    CookieEntry,
    SessionData,
    SessionInfo,
    session_to_info,
)


class SessionNotFound(KeyError):
    """Raised when a session file does not exist."""


class SessionStore:
    """Fernet-encrypted session file store. One file per session."""

    def __init__(self, store_path: str, encryption_key: str) -> None:
        self._store_dir = Path(store_path)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        key_bytes = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        self._fernet = Fernet(key_bytes)

    def _path(self, session_id: str) -> Path:
        return self._store_dir / f"{session_id}.enc"

    def save(self, session: SessionData) -> None:
        raw = _session_to_json(session)
        encrypted = self._fernet.encrypt(raw.encode())
        self._path(session.session_id).write_bytes(encrypted)

    def load(self, session_id: str) -> SessionData:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFound(session_id)
        try:
            encrypted = path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            return _json_to_session(decrypted.decode())
        except (InvalidToken, json.JSONDecodeError, KeyError) as exc:
            raise ValueError(
                f"Failed to decrypt/parse session {session_id!r}: {exc}"
            ) from exc

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            path.unlink()

    def list_sessions(self) -> list[SessionInfo]:
        results: list[SessionInfo] = []
        for enc_path in sorted(self._store_dir.glob("*.enc")):
            session_id = enc_path.stem
            try:
                data = self.load(session_id)
                results.append(session_to_info(data))
            except Exception:
                continue
        return results

    def exists(self, session_id: str) -> bool:
        return self._path(session_id).exists()


def _session_to_json(session: SessionData) -> str:
    """Serialize SessionData to JSON string."""
    return json.dumps({
        "session_id": session.session_id,
        "browser_service": session.browser_service,
        "domain": session.domain,
        "cookies": [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "expiry": c.expiry,
                "secure": c.secure,
                "http_only": c.http_only,
            }
            for c in session.cookies
        ],
        "user_agent": session.user_agent,
        "fingerprint_id": session.fingerprint_id,
        "created_ts": session.created_ts,
        "last_used_ts": session.last_used_ts,
        "expiry_ts": session.expiry_ts,
        "proxy_binding": session.proxy_binding,
        "strict_proxy": session.strict_proxy,
        "use_count": session.use_count,
        "local_storage": session.local_storage,
    }, default=str)


def _json_to_session(raw: str) -> SessionData:
    """Deserialize JSON string to SessionData."""
    data = json.loads(raw)
    cookies = [CookieEntry(**c) for c in data.get("cookies", [])]
    return SessionData(
        session_id=data["session_id"],
        browser_service=data["browser_service"],
        domain=data["domain"],
        cookies=cookies,
        user_agent=data["user_agent"],
        fingerprint_id=data["fingerprint_id"],
        created_ts=data["created_ts"],
        last_used_ts=data["last_used_ts"],
        expiry_ts=data.get("expiry_ts"),
        proxy_binding=data.get("proxy_binding"),
        strict_proxy=data.get("strict_proxy", False),
        use_count=data.get("use_count", 0),
        local_storage=data.get("local_storage"),
    )
