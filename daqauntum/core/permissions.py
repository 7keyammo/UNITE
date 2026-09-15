from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    name: str
    required_level: int
    irreversible: bool = False


@dataclass(frozen=True)
class PermissionDecision:
    outcome: str  # "execute" | "confirm" | "deny"
    reason: str

    @property
    def can_execute(self) -> bool:
        return self.outcome == "execute"

    @property
    def requires_confirmation(self) -> bool:
        return self.outcome == "confirm"


class PermissionManager:
    """Enforce the L0-L4 policy in the constitution.

    L0: read-only
    L1: recommendations only
    L2: may prepare consequential actions, but not execute them
    L3: may execute consequential actions only after explicit approval
    L4: may autonomously execute actions inside the configured tool surface,
        except actions that are always-confirm or irreversible
    """

    ALWAYS_CONFIRM = {"send_email", "purchase", "delete_file", "publish", "change_credentials"}

    def __init__(self, level: int = 2):
        if level < 0 or level > 4:
            raise ValueError("Permission level must be between 0 and 4")
        self.level = level

    def evaluate(self, action: Action) -> PermissionDecision:
        if action.required_level < 0 or action.required_level > 4:
            return PermissionDecision("deny", "Tool declared an invalid permission level")

        # Read-only operations are allowed at every level.
        if action.required_level == 0:
            return PermissionDecision("execute", "Read-only action")

        # L0/L1 cannot prepare or execute state-changing actions.
        if self.level < 2:
            return PermissionDecision("deny", f"Current permission level L{self.level} cannot prepare state-changing actions")

        # Explicitly sensitive or irreversible actions always stop for approval.
        if action.name in self.ALWAYS_CONFIRM or action.irreversible:
            return PermissionDecision("confirm", "Action always requires explicit approval")

        # L2 is preparation-only for anything that changes state.
        if self.level == 2:
            return PermissionDecision("confirm", "L2 may prepare this action but requires approval to execute")

        # L3 means execution only after approval for state-changing actions.
        if self.level == 3:
            return PermissionDecision("confirm", "L3 executes state-changing actions only after explicit approval")

        # L4 can execute only if the action itself is within the declared tool level.
        if self.level >= action.required_level:
            return PermissionDecision("execute", "Allowed by L4 autonomy within the registered tool surface")

        return PermissionDecision("deny", f"Action requires L{action.required_level}; current level is L{self.level}")

    # Backward-compatible helpers for code that used the v0.2 API.
    def can_prepare(self, action: Action) -> bool:
        return self.evaluate(action).outcome in {"execute", "confirm"}

    def requires_confirmation(self, action: Action) -> bool:
        return self.evaluate(action).requires_confirmation
