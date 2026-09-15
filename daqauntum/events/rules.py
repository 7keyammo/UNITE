from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from events.conditions import ConditionError, evaluate_all, validate_condition
from events.models import Event, normalize_severity, severity_at_least


# A reaction may inform the user, remember work to do, or propose an action.
# It may never carry an action out. Execution stays with the kernel's tool
# registry behind the permission gate, where the user can see and approve it.
ACTION_TYPES: tuple[str, ...] = ("notify", "queue_task", "propose_tool")

PROPOSAL_STATUSES: tuple[str, ...] = ("pending_approval", "denied", "approved", "rejected", "expired")

TASK_STATUSES: tuple[str, ...] = ("open", "done", "dismissed")

_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")

_MAX_RULES = 500


class RuleError(ValueError):
    """Raised when a rule definition is invalid."""


def render_template(text: Any, event: Event) -> str:
    """Substitute {{path}} placeholders from the event only.

    This is deliberately not a template engine: there is no expression
    evaluation, no attribute traversal outside the event, and an unknown path
    renders as an empty string rather than raising at fire time.
    """
    def _replace(match: re.Match[str]) -> str:
        value = event.get(match.group(1))
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    return _TEMPLATE_RE.sub(_replace, str(text or ""))


def validate_action(action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise RuleError("Rule requires an 'action' object")
    action_type = str(action.get("type") or "").strip().lower()
    if action_type not in ACTION_TYPES:
        raise RuleError(f"Unsupported action type: {action_type or '(missing)'}. Allowed: {', '.join(ACTION_TYPES)}")
    normalized: dict[str, Any] = {"type": action_type}
    if action_type == "notify":
        title = str(action.get("title") or "").strip()
        if not title:
            raise RuleError("notify action requires a 'title'")
        normalized["title"] = title
        normalized["body"] = str(action.get("body") or "")
        if action.get("severity") is not None:
            normalized["severity"] = normalize_severity(action.get("severity"))
    elif action_type == "queue_task":
        title = str(action.get("title") or "").strip()
        if not title:
            raise RuleError("queue_task action requires a 'title'")
        normalized["title"] = title
        normalized["detail"] = str(action.get("detail") or "")
        if action.get("project"):
            normalized["project"] = str(action.get("project"))
        normalized["notify"] = bool(action.get("notify", True))
    else:  # propose_tool
        tool = str(action.get("tool") or "").strip()
        if not tool:
            raise RuleError("propose_tool action requires a 'tool' name")
        arguments = action.get("arguments", {})
        if not isinstance(arguments, dict):
            raise RuleError("propose_tool 'arguments' must be an object")
        normalized["tool"] = tool
        normalized["arguments"] = arguments
        normalized["reason"] = str(action.get("reason") or "")
    return normalized


def validate_scope(scope: Any) -> dict[str, Any]:
    """Validate the optional scope that narrows where a rule may apply."""
    if scope in (None, ""):
        return {}
    if not isinstance(scope, dict):
        raise RuleError("Rule 'scope' must be an object")
    normalized: dict[str, Any] = {}
    for key in ("sources", "subjects", "projects"):
        value = scope.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise RuleError(f"Rule scope '{key}' must be a string or list of strings")
        normalized[key] = [str(item).strip().lower() for item in value if str(item).strip()]
    hours = scope.get("hours")
    if hours not in (None, ""):
        if not isinstance(hours, (list, tuple)) or len(hours) != 2:
            raise RuleError("Rule scope 'hours' must be [start_hour, end_hour]")
        try:
            start, end = int(hours[0]), int(hours[1])
        except (TypeError, ValueError) as exc:
            raise RuleError("Rule scope 'hours' must contain integers") from exc
        if not (0 <= start <= 23 and 0 <= end <= 23):
            raise RuleError("Rule scope 'hours' must be between 0 and 23")
        normalized["hours"] = [start, end]
    return normalized


class ReactionEngine:
    """Deterministic rules that turn accepted events into advisory reactions.

    Three hard limits define this subsystem:

    1. Matching is deterministic. No model decides whether a rule fires.
    2. A rule's only outputs are a notification, a queued task, or a tool
       *proposal*. There is no execution path here at all.
    3. A proposal is always checked against the permission manager, and a
       proposal the gate would deny is recorded as denied rather than queued.
       Even a proposal the gate would allow still waits for the user, because
       a passive observation must not become an autonomous action.
    """

    def __init__(
        self,
        memory,
        notifications,
        *,
        config: dict[str, Any] | None = None,
        tool_registry=None,
        permission_manager=None,
    ):
        self.memory = memory
        self.notifications = notifications
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        self.default_cooldown = max(0.0, float(self.config.get("default_cooldown_seconds", 900)))
        self.max_reactions_per_event = max(1, int(self.config.get("max_reactions_per_event", 8)))
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager
        self._lock = threading.RLock()
        self._ensure_schema()
        if self.config.get("builtin_rules", True):
            self._install_builtin_rules()

    # Schema ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                match_source TEXT,
                match_kind TEXT,
                match_subject TEXT,
                min_severity TEXT NOT NULL DEFAULT 'debug',
                conditions_json TEXT NOT NULL DEFAULT '[]',
                action_json TEXT NOT NULL,
                scope_json TEXT NOT NULL DEFAULT '{}',
                cooldown_seconds REAL NOT NULL DEFAULT 900,
                expires_at REAL,
                origin TEXT NOT NULL DEFAULT 'user',
                last_fired_at REAL,
                fire_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                project TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                rule_id INTEGER,
                event_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool TEXT NOT NULL,
                arguments_json TEXT NOT NULL DEFAULT '{}',
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending_approval',
                permission_outcome TEXT NOT NULL DEFAULT '',
                permission_reason TEXT NOT NULL DEFAULT '',
                required_level INTEGER,
                irreversible INTEGER NOT NULL DEFAULT 0,
                rule_id INTEGER,
                event_id INTEGER,
                resolved_at REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_reaction_rules_enabled ON reaction_rules(enabled)",
            "CREATE INDEX IF NOT EXISTS idx_reaction_rules_kind ON reaction_rules(match_kind)",
            "CREATE INDEX IF NOT EXISTS idx_reaction_tasks_status ON reaction_tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_reaction_proposals_status ON reaction_proposals(status)",
        ):
            self.memory.conn.execute(statement)
        self.memory.conn.commit()

    def _install_builtin_rules(self) -> None:
        """Seed the documented starter rules once.

        Built-in rules are notify-only. Shipping a rule that proposes an action
        would mean the product chose an action on the user's behalf.
        """
        if self.memory.get_state("reactions.builtin_installed", False):
            return
        starters = [
            {
                "name": "low-battery-warning",
                "description": "Warn once per hour when the battery is low and the machine is not charging.",
                "match_kind": "low_battery",
                "conditions": [{"path": "attributes.percent", "op": "lt", "value": 15}],
                "action": {
                    "type": "notify",
                    "title": "Battery low ({{attributes.percent}}%)",
                    "body": "{{message}} on {{subject}}. Plug in to avoid an unexpected shutdown.",
                    "severity": "warning",
                },
                "cooldown_seconds": 3600,
            },
            {
                "name": "high-temperature-warning",
                "description": "Warn when an approved thermal sensor reports a sustained high temperature.",
                "match_kind": "high_temperature",
                "action": {
                    "type": "notify",
                    "title": "High temperature on {{subject}}",
                    "body": "{{message}}",
                    "severity": "warning",
                },
                "cooldown_seconds": 1800,
            },
            {
                "name": "network-change-notice",
                "description": "Note when the machine changes Wi-Fi network or goes offline.",
                "match_kind": "wifi_change",
                "action": {
                    "type": "notify",
                    "title": "Network changed",
                    "body": "{{message}}",
                    "severity": "info",
                },
                "cooldown_seconds": 300,
            },
        ]
        for rule in starters:
            try:
                self.create_rule(origin="builtin", **rule)
            except RuleError:
                continue
        self.memory.set_state("reactions.builtin_installed", True)

    # Rule CRUD ---------------------------------------------------------------
    def create_rule(
        self,
        *,
        name: str,
        action: dict[str, Any],
        description: str = "",
        match_source: str | None = None,
        match_kind: str | None = None,
        match_subject: str | None = None,
        min_severity: str = "debug",
        conditions: list[dict[str, Any]] | None = None,
        scope: dict[str, Any] | None = None,
        cooldown_seconds: float | None = None,
        expires_at: float | None = None,
        expires_in_seconds: float | None = None,
        enabled: bool = True,
        origin: str = "user",
    ) -> dict[str, Any]:
        name = str(name or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.\-]{1,63}", name):
            raise RuleError("Rule name must be 2-64 chars of letters, digits, '-', '_' or '.'")
        normalized_action = validate_action(action)
        normalized_scope = validate_scope(scope)
        try:
            normalized_conditions = [validate_condition(item) for item in (conditions or [])]
        except ConditionError as exc:
            raise RuleError(str(exc)) from exc
        if expires_at is None and expires_in_seconds is not None:
            expires_at = time.time() + max(0.0, float(expires_in_seconds))
        cooldown = self.default_cooldown if cooldown_seconds is None else max(0.0, float(cooldown_seconds))

        with self._lock:
            total = self.memory.conn.execute("SELECT COUNT(*) AS n FROM reaction_rules").fetchone()
            if int(total["n"] if total else 0) >= _MAX_RULES:
                raise RuleError(f"Rule limit reached ({_MAX_RULES}). Delete unused rules first.")
            existing = self.memory.conn.execute(
                "SELECT id FROM reaction_rules WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                raise RuleError(f"A rule named '{name}' already exists")
            cursor = self.memory.conn.execute(
                """
                INSERT INTO reaction_rules(
                    name, description, enabled, match_source, match_kind, match_subject,
                    min_severity, conditions_json, action_json, scope_json,
                    cooldown_seconds, expires_at, origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    str(description or ""),
                    1 if enabled else 0,
                    str(match_source).strip().lower() if match_source else None,
                    str(match_kind).strip().lower() if match_kind else None,
                    str(match_subject).strip().lower() if match_subject else None,
                    normalize_severity(min_severity, "debug"),
                    json.dumps(normalized_conditions, ensure_ascii=False),
                    json.dumps(normalized_action, ensure_ascii=False),
                    json.dumps(normalized_scope, ensure_ascii=False),
                    cooldown,
                    float(expires_at) if expires_at is not None else None,
                    str(origin or "user"),
                ),
            )
            self.memory.conn.commit()
            rule_id = int(cursor.lastrowid)
        self.memory.add_event("reaction_rule_created", {"rule_id": rule_id, "name": name, "origin": origin})
        return self.get_rule(rule_id) or {}

    def update_rule(self, rule_id: int, **changes: Any) -> dict[str, Any] | None:
        rule = self.get_rule(rule_id)
        if not rule:
            return None
        assignments: list[str] = []
        params: list[Any] = []
        if "enabled" in changes:
            assignments.append("enabled = ?")
            params.append(1 if changes["enabled"] else 0)
        if "description" in changes:
            assignments.append("description = ?")
            params.append(str(changes["description"] or ""))
        if "cooldown_seconds" in changes and changes["cooldown_seconds"] is not None:
            assignments.append("cooldown_seconds = ?")
            params.append(max(0.0, float(changes["cooldown_seconds"])))
        if "expires_at" in changes:
            value = changes["expires_at"]
            assignments.append("expires_at = ?")
            params.append(float(value) if value is not None else None)
        if "conditions" in changes:
            try:
                conditions = [validate_condition(item) for item in (changes["conditions"] or [])]
            except ConditionError as exc:
                raise RuleError(str(exc)) from exc
            assignments.append("conditions_json = ?")
            params.append(json.dumps(conditions, ensure_ascii=False))
        if "action" in changes:
            assignments.append("action_json = ?")
            params.append(json.dumps(validate_action(changes["action"]), ensure_ascii=False))
        if "scope" in changes:
            assignments.append("scope_json = ?")
            params.append(json.dumps(validate_scope(changes["scope"]), ensure_ascii=False))
        if not assignments:
            return rule
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        params.append(int(rule_id))
        with self._lock:
            self.memory.conn.execute(
                f"UPDATE reaction_rules SET {', '.join(assignments)} WHERE id = ?", params
            )
            self.memory.conn.commit()
        self.memory.add_event("reaction_rule_updated", {"rule_id": int(rule_id), "fields": sorted(changes)})
        return self.get_rule(rule_id)

    def delete_rule(self, rule_id: int) -> bool:
        with self._lock:
            cursor = self.memory.conn.execute("DELETE FROM reaction_rules WHERE id = ?", (int(rule_id),))
            self.memory.conn.commit()
        deleted = bool(cursor.rowcount)
        if deleted:
            self.memory.add_event("reaction_rule_deleted", {"rule_id": int(rule_id)})
        return deleted

    @staticmethod
    def _rule_row(row: Any) -> dict[str, Any]:
        data = dict(row)
        for source_field, target in (("conditions_json", "conditions"), ("action_json", "action"), ("scope_json", "scope")):
            raw = data.pop(source_field, None)
            try:
                data[target] = json.loads(raw or ("[]" if target == "conditions" else "{}"))
            except (TypeError, ValueError):
                data[target] = [] if target == "conditions" else {}
        data["enabled"] = bool(data.get("enabled"))
        expires_at = data.get("expires_at")
        data["expired"] = bool(expires_at is not None and float(expires_at) <= time.time())
        return data

    def get_rule(self, rule_id: int) -> dict[str, Any] | None:
        row = self.memory.conn.execute(
            "SELECT * FROM reaction_rules WHERE id = ?", (int(rule_id),)
        ).fetchone()
        return self._rule_row(row) if row else None

    def list_rules(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled = 1" if enabled_only else ""
        rows = self.memory.conn.execute(
            f"SELECT * FROM reaction_rules {where} ORDER BY id ASC"
        ).fetchall()
        return [self._rule_row(row) for row in rows]

    # Matching ----------------------------------------------------------------
    def _rule_applies(self, rule: dict[str, Any], event: Event, now: float) -> tuple[bool, str]:
        if not rule.get("enabled"):
            return False, "rule-disabled"
        expires_at = rule.get("expires_at")
        if expires_at is not None and float(expires_at) <= now:
            return False, "rule-expired"
        if rule.get("match_source") and rule["match_source"] != event.source:
            return False, "source-mismatch"
        if rule.get("match_kind") and rule["match_kind"] != event.kind:
            return False, "kind-mismatch"
        if rule.get("match_subject") and rule["match_subject"] != event.subject:
            return False, "subject-mismatch"
        if not severity_at_least(event.severity, rule.get("min_severity", "debug")):
            return False, "below-rule-severity"

        scope = rule.get("scope") or {}
        if scope.get("sources") and event.source not in scope["sources"]:
            return False, "out-of-scope-source"
        if scope.get("subjects") and event.subject not in scope["subjects"]:
            return False, "out-of-scope-subject"
        hours = scope.get("hours")
        if hours:
            start, end = int(hours[0]), int(hours[1])
            hour = time.localtime(event.occurred_at).tm_hour
            inside = start <= hour < end if start <= end else (hour >= start or hour < end)
            if not inside:
                return False, "outside-scope-hours"

        cooldown = float(rule.get("cooldown_seconds") or 0.0)
        last_fired = rule.get("last_fired_at")
        if cooldown > 0 and last_fired is not None and (now - float(last_fired)) < cooldown:
            return False, "rule-cooldown"

        if not evaluate_all(event, rule.get("conditions") or []):
            return False, "conditions-not-met"
        return True, "match"

    def react(self, event: Event) -> list[dict[str, Any]]:
        """Evaluate every rule against one accepted event.

        Returns a description of each reaction taken, so the caller and the
        audit log agree on what happened.
        """
        if not self.enabled:
            return []
        now = time.time()
        reactions: list[dict[str, Any]] = []
        for rule in self.list_rules():
            if len(reactions) >= self.max_reactions_per_event:
                break
            applies, _reason = self._rule_applies(rule, event, now)
            if not applies:
                continue
            try:
                outcome = self._apply_action(rule, event)
            except Exception as exc:  # pragma: no cover - defensive
                outcome = {"type": rule.get("action", {}).get("type"), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self.memory.add_event("reaction_failed", {"rule_id": rule["id"], "error": outcome["error"]})
            outcome["rule_id"] = rule["id"]
            outcome["rule"] = rule["name"]
            reactions.append(outcome)
            self._mark_fired(rule["id"], now)
        return reactions

    def _mark_fired(self, rule_id: int, now: float) -> None:
        self.memory.conn.execute(
            "UPDATE reaction_rules SET last_fired_at = ?, fire_count = fire_count + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (now, int(rule_id)),
        )
        self.memory.conn.commit()

    # Actions -----------------------------------------------------------------
    def _apply_action(self, rule: dict[str, Any], event: Event) -> dict[str, Any]:
        action = rule.get("action") or {}
        action_type = action.get("type")
        if action_type == "notify":
            return self._action_notify(rule, action, event)
        if action_type == "queue_task":
            return self._action_queue_task(rule, action, event)
        if action_type == "propose_tool":
            return self._action_propose_tool(rule, action, event)
        return {"type": action_type, "ok": False, "error": "unsupported-action"}

    def _action_notify(self, rule: dict[str, Any], action: dict[str, Any], event: Event) -> dict[str, Any]:
        record = self.notifications.create(
            render_template(action.get("title"), event),
            body=render_template(action.get("body"), event),
            severity=action.get("severity") or event.severity,
            source=event.source,
            kind=event.kind,
            event_id=event.event_id,
            rule_id=rule["id"],
            payload={"event": event.as_dict(), "rule": rule["name"]},
        )
        return {"type": "notify", "ok": record is not None, "notification_id": (record or {}).get("id")}

    def _action_queue_task(self, rule: dict[str, Any], action: dict[str, Any], event: Event) -> dict[str, Any]:
        title = render_template(action.get("title"), event)
        detail = render_template(action.get("detail"), event)
        cursor = self.memory.conn.execute(
            "INSERT INTO reaction_tasks(title, detail, project, rule_id, event_id) VALUES (?, ?, ?, ?, ?)",
            (title, detail, action.get("project"), rule["id"], event.event_id),
        )
        self.memory.conn.commit()
        task_id = int(cursor.lastrowid)
        notification_id = None
        if action.get("notify", True):
            record = self.notifications.create(
                f"Task queued: {title}",
                body=detail or f"Queued from rule '{rule['name']}'.",
                severity="notice",
                source=event.source,
                kind="queued_task",
                event_id=event.event_id,
                rule_id=rule["id"],
                payload={"task_id": task_id},
            )
            notification_id = (record or {}).get("id")
        return {"type": "queue_task", "ok": True, "task_id": task_id, "notification_id": notification_id}

    def _action_propose_tool(self, rule: dict[str, Any], action: dict[str, Any], event: Event) -> dict[str, Any]:
        """Record a tool call the user may choose to approve.

        Nothing is executed here under any permission level. The permission
        manager is consulted so a proposal the gate would refuse is stored as
        denied instead of sitting in the queue looking actionable, but an
        allowed proposal still waits: an observation is not an instruction.
        """
        tool_name = str(action.get("tool"))
        arguments = {
            key: (render_template(value, event) if isinstance(value, str) else value)
            for key, value in (action.get("arguments") or {}).items()
        }
        spec = self.tool_registry.get(tool_name) if self.tool_registry else None
        if spec is None:
            status, outcome, reason = "denied", "deny", f"Unknown tool '{tool_name}'"
            required_level, irreversible = None, False
        else:
            required_level, irreversible = spec.required_level, bool(spec.irreversible)
            if self.permission_manager is not None:
                from core.permissions import Action as PermissionAction

                decision = self.permission_manager.evaluate(
                    PermissionAction(spec.name, spec.required_level, spec.irreversible)
                )
                outcome, reason = decision.outcome, decision.reason
                status = "denied" if decision.outcome == "deny" else "pending_approval"
            else:
                status, outcome, reason = "pending_approval", "confirm", "No permission manager configured; approval required"

        cursor = self.memory.conn.execute(
            """
            INSERT INTO reaction_proposals(
                tool, arguments_json, reason, status, permission_outcome,
                permission_reason, required_level, irreversible, rule_id, event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_name,
                json.dumps(arguments, ensure_ascii=False, default=str),
                render_template(action.get("reason"), event),
                status,
                outcome,
                reason,
                required_level,
                1 if irreversible else 0,
                rule["id"],
                event.event_id,
            ),
        )
        self.memory.conn.commit()
        proposal_id = int(cursor.lastrowid)

        record = self.notifications.create(
            f"Action proposed: {tool_name}",
            body=(
                f"Rule '{rule['name']}' proposed {tool_name} after: {event.message or event.kind}. "
                + ("This proposal was denied by the current permission level and will not run."
                   if status == "denied"
                   else "It will not run until you approve it.")
            ),
            severity="notice" if status == "pending_approval" else "info",
            source=event.source,
            kind="proposed_action",
            event_id=event.event_id,
            rule_id=rule["id"],
            payload={"proposal_id": proposal_id, "tool": tool_name, "arguments": arguments, "status": status},
        )
        self.memory.add_event(
            "reaction_proposal_created",
            {"proposal_id": proposal_id, "tool": tool_name, "status": status, "rule_id": rule["id"]},
        )
        return {
            "type": "propose_tool",
            "ok": True,
            "executed": False,
            "proposal_id": proposal_id,
            "status": status,
            "permission_outcome": outcome,
            "notification_id": (record or {}).get("id"),
        }

    # Tasks and proposals -----------------------------------------------------
    def list_tasks(self, *, status: str | None = "open", limit: int = 50) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(str(status))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 200)))
        rows = self.memory.conn.execute(
            f"SELECT * FROM reaction_tasks {where} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
        return [dict(row) for row in rows]

    def set_task_status(self, task_id: int, status: str) -> dict[str, Any] | None:
        status = str(status or "").strip().lower()
        if status not in TASK_STATUSES:
            raise ValueError(f"Unknown task status: {status}")
        self.memory.conn.execute(
            "UPDATE reaction_tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, int(task_id)),
        )
        self.memory.conn.commit()
        row = self.memory.conn.execute("SELECT * FROM reaction_tasks WHERE id = ?", (int(task_id),)).fetchone()
        return dict(row) if row else None

    def list_proposals(self, *, status: str | None = "pending_approval", limit: int = 50) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(str(status))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 200)))
        rows = self.memory.conn.execute(
            f"SELECT * FROM reaction_proposals {where} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
        proposals = []
        for row in rows:
            data = dict(row)
            try:
                data["arguments"] = json.loads(data.pop("arguments_json") or "{}")
            except (TypeError, ValueError):
                data["arguments"] = {}
            data["irreversible"] = bool(data.get("irreversible"))
            proposals.append(data)
        return proposals

    def get_proposal(self, proposal_id: int) -> dict[str, Any] | None:
        matches = [p for p in self.list_proposals(status="all", limit=200) if p["id"] == int(proposal_id)]
        return matches[0] if matches else None

    def resolve_proposal(self, proposal_id: int, status: str) -> dict[str, Any] | None:
        """Record the user's decision about a proposal.

        This marks the proposal only. Running the tool remains the kernel's
        job through the normal approval path, so approving here cannot become
        a second execution route.
        """
        status = str(status or "").strip().lower()
        if status not in {"approved", "rejected", "expired"}:
            raise ValueError(f"Cannot set proposal status to: {status}")
        self.memory.conn.execute(
            "UPDATE reaction_proposals SET status = ?, resolved_at = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending_approval'",
            (status, time.time(), int(proposal_id)),
        )
        self.memory.conn.commit()
        self.memory.add_event("reaction_proposal_resolved", {"proposal_id": int(proposal_id), "status": status})
        return self.get_proposal(proposal_id)

    def stats(self) -> dict[str, Any]:
        rules = self.list_rules()
        open_tasks = self.memory.conn.execute(
            "SELECT COUNT(*) AS n FROM reaction_tasks WHERE status = 'open'"
        ).fetchone()
        pending = self.memory.conn.execute(
            "SELECT COUNT(*) AS n FROM reaction_proposals WHERE status = 'pending_approval'"
        ).fetchone()
        denied = self.memory.conn.execute(
            "SELECT COUNT(*) AS n FROM reaction_proposals WHERE status = 'denied'"
        ).fetchone()
        return {
            "enabled": self.enabled,
            "rules": len(rules),
            "rules_enabled": sum(1 for rule in rules if rule["enabled"] and not rule["expired"]),
            "rules_expired": sum(1 for rule in rules if rule["expired"]),
            "fires": sum(int(rule.get("fire_count") or 0) for rule in rules),
            "open_tasks": int(open_tasks["n"] if open_tasks else 0),
            "pending_proposals": int(pending["n"] if pending else 0),
            "denied_proposals": int(denied["n"] if denied else 0),
            "proposals_executed": 0,
        }
