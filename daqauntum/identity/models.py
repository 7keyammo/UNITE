from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


# A scope says which surfaces a device may reach. It never says what DaQauntum
# is allowed to do: that remains the permission level, decided by the
# PermissionManager. A device with "approve" can ask for a pending action to be
# carried out; it cannot make an action permissible that the gate would refuse.
SCOPES: dict[str, str] = {
    "read": "Read status, events, notifications and memory summaries.",
    "chat": "Hold a conversation and use read-only tools.",
    "approve": "Approve actions that are already awaiting approval.",
    "ingest": "Send files, notes and captures into connected sources.",
    "admin": "Enrol and revoke devices.",
}

DEFAULT_SCOPES: tuple[str, ...] = ("read", "chat")

DEVICE_STATUSES: tuple[str, ...] = ("active", "revoked")

# Human-typed enrollment codes use an unambiguous alphabet: no O/0, I/1, L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8

# The token itself is high-entropy, so a plain SHA-256 of it is enough to make
# the stored form useless to an attacker. The enrollment code is short enough to
# be typed, so it gets a deliberately slow KDF as well as rate limiting.
CODE_KDF_ITERATIONS = 200_000
CODE_KDF_SALT_BYTES = 16

TOKEN_PREFIX = "dq_"


def normalize_scopes(scopes: Any) -> list[str]:
    """Keep only recognised scopes, in a stable order, without duplicates.

    An unknown scope is dropped rather than rejected so that a newer client
    asking for a future scope degrades to less access, never to more.
    """
    if isinstance(scopes, str):
        scopes = [scopes]
    if not isinstance(scopes, (list, tuple, set)):
        return list(DEFAULT_SCOPES)
    seen = {str(scope).strip().lower() for scope in scopes}
    ordered = [scope for scope in SCOPES if scope in seen]
    return ordered or list(DEFAULT_SCOPES)


def generate_enrollment_code() -> str:
    """Create a single-use code the user reads off the trusted host."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def canonical_code(code: str) -> str:
    """Normalize typed input: drop separators and whitespace, upcase.

    No character substitution is needed because CODE_ALPHABET already excludes
    every ambiguous pair (O/0, I/1/L), so a code cannot be misread into a
    different valid code.
    """
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def hash_code(code: str, salt: bytes | None = None) -> tuple[str, str]:
    """Return (hex digest, hex salt) for an enrollment code."""
    salt = salt or secrets.token_bytes(CODE_KDF_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", canonical_code(code).encode("utf-8"), salt, CODE_KDF_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_code(code: str, digest_hex: str, salt_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate, _ = hash_code(code, salt)
    return hmac.compare_digest(candidate, digest_hex)


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    """A short, non-secret label so a token can be identified in a list."""
    return str(token or "")[: len(TOKEN_PREFIX) + 6]


@dataclass
class Device:
    device_id: str
    name: str
    platform: str = "unknown"
    status: str = "active"
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    enrolled_at: float = field(default_factory=time.time)
    revoked_at: float | None = None
    last_seen_at: float | None = None
    last_remote: str | None = None
    note: str = ""

    @property
    def active(self) -> bool:
        return self.status == "active" and self.revoked_at is None

    def has_scope(self, scope: str) -> bool:
        return self.active and str(scope).strip().lower() in self.scopes

    def as_dict(self, *, redact_remote: bool = False) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "platform": self.platform,
            "status": self.status,
            "active": self.active,
            "scopes": list(self.scopes),
            "enrolled_at": self.enrolled_at,
            "revoked_at": self.revoked_at,
            "last_seen_at": self.last_seen_at,
            "last_remote": None if redact_remote else self.last_remote,
            "note": self.note,
        }

    @classmethod
    def from_row(cls, row: Any) -> "Device":
        import json

        data = dict(row)
        try:
            scopes = json.loads(data.get("scopes_json") or "[]")
        except (TypeError, ValueError):
            scopes = []
        return cls(
            device_id=data["device_id"],
            name=data.get("name") or "device",
            platform=data.get("platform") or "unknown",
            status=data.get("status") or "active",
            scopes=normalize_scopes(scopes),
            enrolled_at=float(data.get("enrolled_at") or 0.0),
            revoked_at=data.get("revoked_at"),
            last_seen_at=data.get("last_seen_at"),
            last_remote=data.get("last_remote"),
            note=data.get("note") or "",
        )


@dataclass
class AuthResult:
    """Outcome of authenticating one request.

    `reason` is for the audit log and the developer. It is deliberately not
    echoed to an unauthenticated caller, who is told only that authentication
    failed, so a probe cannot learn whether a token exists, is expired, or
    belongs to a revoked device.
    """

    ok: bool
    reason: str
    device: Device | None = None
    scopes: list[str] = field(default_factory=list)
    token_prefix: str | None = None

    def has_scope(self, scope: str) -> bool:
        return self.ok and str(scope).strip().lower() in self.scopes
