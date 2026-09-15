from __future__ import annotations

import json
import secrets
import threading
import time
from typing import Any

from identity.models import (
    DEFAULT_SCOPES,
    SCOPES,
    AuthResult,
    Device,
    generate_enrollment_code,
    generate_token,
    hash_code,
    hash_token,
    normalize_scopes,
    token_prefix,
    verify_code,
)


class IdentityError(RuntimeError):
    """Raised for enrollment and device-management failures."""


class IdentityManager:
    """Device identity, enrollment and session tokens for remote clients.

    The design rule that matters most: **identity is not authority**. A device
    proves which client is calling and which surfaces it may reach. What
    DaQauntum is then allowed to do is still decided by the PermissionManager
    at the configured level. An enrolled phone with the "approve" scope can ask
    for an action that is already awaiting approval to proceed; it cannot make
    a forbidden action permissible, and it cannot raise the permission level.

    Secrets are never stored in usable form. Session tokens are kept as SHA-256
    digests, and the short human-typed enrollment code is kept as a slow
    PBKDF2 digest, so reading the database does not yield a working credential.
    """

    def __init__(self, memory, config: dict[str, Any] | None = None):
        self.memory = memory
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        self.code_ttl_seconds = max(60, int(self.config.get("enrollment_code_ttl_seconds", 600)))
        self.token_ttl_seconds = max(300, int(self.config.get("token_ttl_seconds", 30 * 86400)))
        self.max_devices = max(1, int(self.config.get("max_devices", 50)))
        self.max_failures = max(3, int(self.config.get("max_auth_failures", 8)))
        self.failure_window = max(30, int(self.config.get("auth_failure_window_seconds", 300)))
        self.require_auth_for_remote = bool(self.config.get("require_auth_for_remote", True))
        self.require_auth_for_loopback = bool(self.config.get("require_auth_for_loopback", False))
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.RLock()
        self._ensure_schema()

    # Schema ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'unknown',
                status TEXT NOT NULL DEFAULT 'active',
                scopes_json TEXT NOT NULL DEFAULT '[]',
                enrolled_at REAL NOT NULL,
                revoked_at REAL,
                last_seen_at REAL,
                last_remote TEXT,
                note TEXT NOT NULL DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                token_prefix TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL,
                last_used_at REAL,
                revoked_at REAL,
                issued_remote TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enrollment_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT NOT NULL,
                code_salt TEXT NOT NULL,
                device_name TEXT NOT NULL DEFAULT '',
                scopes_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL,
                used_by_device TEXT
            )
            """
        )
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                device_id TEXT,
                remote TEXT,
                detail TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_device_tokens_hash ON device_tokens(token_hash)",
            "CREATE INDEX IF NOT EXISTS idx_device_tokens_device ON device_tokens(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status)",
            "CREATE INDEX IF NOT EXISTS idx_auth_events_id ON auth_events(id DESC)",
        ):
            self.memory.conn.execute(statement)
        self.memory.conn.commit()

    # Audit -------------------------------------------------------------------
    def audit(self, event: str, *, device_id: str | None = None, remote: str | None = None, detail: str = "") -> None:
        """Record an authentication event.

        Never records a secret: codes and tokens appear only as prefixes.
        """
        self.memory.conn.execute(
            "INSERT INTO auth_events(event, device_id, remote, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(event), device_id, remote, str(detail)[:500], time.time()),
        )
        self.memory.conn.execute(
            "DELETE FROM auth_events WHERE id NOT IN (SELECT id FROM auth_events ORDER BY id DESC LIMIT 2000)"
        )
        self.memory.conn.commit()

    def recent_auth_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.memory.conn.execute(
            "SELECT * FROM auth_events ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),)
        ).fetchall()
        return [dict(row) for row in rows]

    # Rate limiting -----------------------------------------------------------
    def _throttled(self, remote: str) -> bool:
        now = time.time()
        with self._lock:
            recent = [at for at in self._failures.get(remote or "?", []) if now - at < self.failure_window]
            self._failures[remote or "?"] = recent
            return len(recent) >= self.max_failures

    def _note_failure(self, remote: str) -> None:
        with self._lock:
            self._failures.setdefault(remote or "?", []).append(time.time())

    def clear_failures(self, remote: str | None = None) -> None:
        with self._lock:
            if remote:
                self._failures.pop(remote, None)
            else:
                self._failures.clear()

    # Enrollment --------------------------------------------------------------
    def create_enrollment_code(
        self,
        *,
        device_name: str = "",
        scopes: Any = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Mint a single-use enrollment code on the trusted host.

        The user chooses the scopes when creating the code, so redeeming it is
        not a second decision to make: the grant was authored here, deliberately,
        and the code is short-lived and single-use.

        The plaintext code is returned exactly once and never stored.
        """
        if not self.enabled:
            raise IdentityError("Device identity is disabled")
        granted = normalize_scopes(scopes if scopes is not None else DEFAULT_SCOPES)
        ttl = max(60, int(ttl_seconds or self.code_ttl_seconds))
        code = generate_enrollment_code()
        digest, salt = hash_code(code)
        now = time.time()
        self.memory.conn.execute(
            """
            INSERT INTO enrollment_codes(code_hash, code_salt, device_name, scopes_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (digest, salt, str(device_name or "")[:120], json.dumps(granted), now, now + ttl),
        )
        # An unused code is superseded rather than left valid alongside the new
        # one, so only the code currently on screen can enrol a device.
        self.memory.conn.execute(
            "UPDATE enrollment_codes SET used_at = ?, used_by_device = 'superseded' "
            "WHERE used_at IS NULL AND expires_at > ? AND code_hash != ?",
            (now, now, digest),
        )
        self.memory.conn.commit()
        self.audit("enrollment_code_created", detail=f"scopes={','.join(granted)} ttl={ttl}s")
        return {"code": code, "scopes": granted, "expires_at": now + ttl, "expires_in_seconds": ttl}

    def redeem_enrollment_code(
        self,
        code: str,
        *,
        device_name: str = "",
        platform: str = "unknown",
        remote: str = "",
    ) -> dict[str, Any]:
        """Exchange a valid code for a device identity and a session token."""
        if not self.enabled:
            raise IdentityError("Device identity is disabled")
        if self._throttled(remote):
            self.audit("enrollment_throttled", remote=remote)
            raise IdentityError("Too many failed attempts. Wait a few minutes and try again.")

        now = time.time()
        rows = self.memory.conn.execute(
            "SELECT * FROM enrollment_codes WHERE used_at IS NULL AND expires_at > ? ORDER BY id DESC", (now,)
        ).fetchall()
        # Every candidate is checked so that timing does not reveal which code
        # exists; there is at most a handful of live codes at any moment.
        matched = None
        for row in rows:
            if verify_code(code, row["code_hash"], row["code_salt"]):
                matched = row
                break
        if matched is None:
            self._note_failure(remote)
            self.audit("enrollment_failed", remote=remote, detail="invalid or expired code")
            raise IdentityError("That enrollment code is not valid or has expired.")

        active = self.memory.conn.execute(
            "SELECT COUNT(*) AS n FROM devices WHERE status = 'active'"
        ).fetchone()
        if int(active["n"] if active else 0) >= self.max_devices:
            raise IdentityError(f"Device limit reached ({self.max_devices}). Revoke a device first.")

        try:
            scopes = normalize_scopes(json.loads(matched["scopes_json"] or "[]"))
        except (TypeError, ValueError):
            scopes = list(DEFAULT_SCOPES)

        device_id = "dev_" + secrets.token_hex(8)
        name = str(device_name or matched["device_name"] or "Paired device")[:120]
        self.memory.conn.execute(
            """
            INSERT INTO devices(device_id, name, platform, status, scopes_json, enrolled_at, last_seen_at, last_remote)
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (device_id, name, str(platform or "unknown")[:60], json.dumps(scopes), now, now, remote or None),
        )
        self.memory.conn.execute(
            "UPDATE enrollment_codes SET used_at = ?, used_by_device = ? WHERE id = ?",
            (now, device_id, matched["id"]),
        )
        self.memory.conn.commit()
        self.clear_failures(remote)
        token = self.issue_token(device_id, remote=remote)
        self.audit("device_enrolled", device_id=device_id, remote=remote, detail=f"name={name} scopes={','.join(scopes)}")
        return {
            "device": self.get_device(device_id).as_dict(),
            "token": token["token"],
            "expires_at": token["expires_at"],
            "scopes": scopes,
        }

    # Tokens ------------------------------------------------------------------
    def issue_token(self, device_id: str, *, remote: str = "", ttl_seconds: int | None = None) -> dict[str, Any]:
        """Issue a session token. The plaintext is returned exactly once."""
        device = self.get_device(device_id)
        if device is None or not device.active:
            raise IdentityError("Unknown or revoked device")
        token = generate_token()
        now = time.time()
        expires_at = now + max(300, int(ttl_seconds or self.token_ttl_seconds))
        self.memory.conn.execute(
            """
            INSERT INTO device_tokens(device_id, token_hash, token_prefix, issued_at, expires_at, issued_remote)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (device_id, hash_token(token), token_prefix(token), now, expires_at, remote or None),
        )
        self.memory.conn.commit()
        self.audit("token_issued", device_id=device_id, remote=remote, detail=token_prefix(token))
        return {"token": token, "expires_at": expires_at, "device_id": device_id}

    def rotate_token(self, token: str, *, remote: str = "") -> dict[str, Any]:
        """Replace a live token with a fresh one and retire the old immediately."""
        result = self.authenticate(token, remote=remote)
        if not result.ok or result.device is None:
            raise IdentityError("Cannot rotate an invalid token")
        self.revoke_token(token)
        return self.issue_token(result.device.device_id, remote=remote)

    def revoke_token(self, token: str) -> bool:
        cursor = self.memory.conn.execute(
            "UPDATE device_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (time.time(), hash_token(token)),
        )
        self.memory.conn.commit()
        if cursor.rowcount:
            self.audit("token_revoked", detail=token_prefix(token))
        return bool(cursor.rowcount)

    # Authentication ----------------------------------------------------------
    def authenticate(self, token: str | None, *, remote: str = "") -> AuthResult:
        """Resolve a bearer token to a device, or explain why it failed.

        Failures are deliberately indistinguishable to the caller. The reason is
        kept here for the audit log so an operator can debug, while a prober
        learns only that authentication failed.
        """
        if not self.enabled:
            return AuthResult(False, "identity-disabled")
        if not token:
            return AuthResult(False, "no-token")
        if self._throttled(remote):
            self.audit("auth_throttled", remote=remote)
            return AuthResult(False, "throttled")

        row = self.memory.conn.execute(
            "SELECT * FROM device_tokens WHERE token_hash = ?", (hash_token(token),)
        ).fetchone()
        if row is None:
            self._note_failure(remote)
            self.audit("auth_failed", remote=remote, detail="unknown token")
            return AuthResult(False, "unknown-token")
        if row["revoked_at"] is not None:
            self._note_failure(remote)
            self.audit("auth_failed", device_id=row["device_id"], remote=remote, detail="revoked token")
            return AuthResult(False, "token-revoked")
        if row["expires_at"] is not None and float(row["expires_at"]) <= time.time():
            self.audit("auth_failed", device_id=row["device_id"], remote=remote, detail="expired token")
            return AuthResult(False, "token-expired")

        device = self.get_device(row["device_id"])
        if device is None or not device.active:
            self._note_failure(remote)
            self.audit("auth_failed", device_id=row["device_id"], remote=remote, detail="device revoked")
            return AuthResult(False, "device-revoked")

        now = time.time()
        self.memory.conn.execute("UPDATE device_tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
        self.memory.conn.execute(
            "UPDATE devices SET last_seen_at = ?, last_remote = ? WHERE device_id = ?",
            (now, remote or None, device.device_id),
        )
        self.memory.conn.commit()
        self.clear_failures(remote)
        return AuthResult(True, "authenticated", device=device, scopes=list(device.scopes),
                          token_prefix=row["token_prefix"])

    # Devices -----------------------------------------------------------------
    def get_device(self, device_id: str) -> Device | None:
        row = self.memory.conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (str(device_id),)
        ).fetchone()
        return Device.from_row(row) if row else None

    def list_devices(self, *, include_revoked: bool = True) -> list[dict[str, Any]]:
        where = "" if include_revoked else "WHERE status = 'active'"
        rows = self.memory.conn.execute(f"SELECT * FROM devices {where} ORDER BY id DESC").fetchall()
        devices = []
        for row in rows:
            device = Device.from_row(row)
            payload = device.as_dict()
            tokens = self.memory.conn.execute(
                "SELECT COUNT(*) AS n FROM device_tokens WHERE device_id = ? AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (device.device_id, time.time()),
            ).fetchone()
            payload["active_tokens"] = int(tokens["n"] if tokens else 0)
            devices.append(payload)
        return devices

    def revoke_device(self, device_id: str) -> bool:
        """Revoke a device and every token it holds, immediately."""
        now = time.time()
        cursor = self.memory.conn.execute(
            "UPDATE devices SET status = 'revoked', revoked_at = ? WHERE device_id = ? AND status = 'active'",
            (now, str(device_id)),
        )
        self.memory.conn.execute(
            "UPDATE device_tokens SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
            (now, str(device_id)),
        )
        self.memory.conn.commit()
        if cursor.rowcount:
            self.audit("device_revoked", device_id=str(device_id))
        return bool(cursor.rowcount)

    def update_device(self, device_id: str, *, name: str | None = None, scopes: Any = None, note: str | None = None) -> dict[str, Any] | None:
        device = self.get_device(device_id)
        if device is None:
            return None
        assignments, params = [], []
        if name is not None:
            assignments.append("name = ?")
            params.append(str(name)[:120])
        if scopes is not None:
            granted = normalize_scopes(scopes)
            assignments.append("scopes_json = ?")
            params.append(json.dumps(granted))
        if note is not None:
            assignments.append("note = ?")
            params.append(str(note)[:500])
        if not assignments:
            return device.as_dict()
        params.append(str(device_id))
        self.memory.conn.execute(f"UPDATE devices SET {', '.join(assignments)} WHERE device_id = ?", params)
        self.memory.conn.commit()
        self.audit("device_updated", device_id=str(device_id), detail=",".join(a.split(" =")[0] for a in assignments))
        return self.get_device(device_id).as_dict()

    # Status ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        now = time.time()
        devices = self.memory.conn.execute(
            "SELECT status, COUNT(*) AS n FROM devices GROUP BY status"
        ).fetchall()
        tokens = self.memory.conn.execute(
            "SELECT COUNT(*) AS n FROM device_tokens WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
            (now,),
        ).fetchone()
        pending = self.memory.conn.execute(
            "SELECT COUNT(*) AS n FROM enrollment_codes WHERE used_at IS NULL AND expires_at > ?", (now,)
        ).fetchone()
        return {
            "enabled": self.enabled,
            "devices": {row["status"]: int(row["n"]) for row in devices},
            "active_tokens": int(tokens["n"] if tokens else 0),
            "open_enrollment_codes": int(pending["n"] if pending else 0),
            "require_auth_for_remote": self.require_auth_for_remote,
            "require_auth_for_loopback": self.require_auth_for_loopback,
            "token_ttl_seconds": self.token_ttl_seconds,
            "max_devices": self.max_devices,
            "scopes": dict(SCOPES),
        }
