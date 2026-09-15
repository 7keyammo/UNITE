"""DaQauntum v0.4.2 device identity, enrollment and session tokens.

Identity says which client is calling and which surfaces it may reach. It never
says what DaQauntum is allowed to do: that stays with the PermissionManager.
"""

from identity.manager import IdentityError, IdentityManager
from identity.models import (
    DEFAULT_SCOPES,
    DEVICE_STATUSES,
    SCOPES,
    AuthResult,
    Device,
    normalize_scopes,
)

__all__ = [
    "DEFAULT_SCOPES",
    "DEVICE_STATUSES",
    "SCOPES",
    "AuthResult",
    "Device",
    "IdentityError",
    "IdentityManager",
    "normalize_scopes",
]
