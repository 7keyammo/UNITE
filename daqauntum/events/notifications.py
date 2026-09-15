from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from events.models import SEVERITY_RANK, normalize_severity


NOTIFICATION_STATUSES: tuple[str, ...] = ("pending", "read", "dismissed")

# Priority is derived from severity so the GUI and any future push adapter
# order notifications the same way without inventing their own scale.
SEVERITY_PRIORITY: dict[str, int] = {
    "debug": 10,
    "info": 30,
    "notice": 50,
    "warning": 70,
    "critical": 90,
}

DeliveryAdapter = Callable[[dict[str, Any]], Any]


class NotificationCenter:
    """Durable, user-facing queue of things DaQauntum wants to say.

    A notification is advisory by construction. It can inform the user and it
    can carry a reference to a proposed action, but creating one never executes
    anything and never raises anyone's permission level.
    """

    def __init__(self, memory, config: dict[str, Any] | None = None):
        self.memory = memory
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        self.max_notifications = max(50, int(self.config.get("max_notifications", 1000)))
        self.min_severity = normalize_severity(self.config.get("min_severity", "info"), "info")
        self._adapters: list[tuple[str, DeliveryAdapter]] = []
        self._lock = threading.RLock()
        self._delivery_failures: list[dict[str, Any]] = []
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'info',
                priority INTEGER NOT NULL DEFAULT 30,
                status TEXT NOT NULL DEFAULT 'pending',
                source TEXT NOT NULL DEFAULT 'runtime',
                kind TEXT NOT NULL DEFAULT 'message',
                event_id INTEGER,
                rule_id INTEGER,
                payload_json TEXT NOT NULL DEFAULT '{}',
                delivered_json TEXT NOT NULL DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_notifications_id ON notifications(id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_priority ON notifications(priority DESC)",
        ):
            self.memory.conn.execute(statement)
        self.memory.conn.commit()

    # Delivery adapters -------------------------------------------------------
    def register_adapter(self, adapter: DeliveryAdapter, *, name: str | None = None) -> None:
        """Register an outbound channel (GUI badge, future mobile push, ...).

        Adapters receive the notification payload only. They are delivery
        surfaces, not decision points, and an adapter failure degrades delivery
        without losing the stored notification.
        """
        if not callable(adapter):
            raise TypeError("Notification adapter must be callable")
        with self._lock:
            self._adapters.append((str(name or getattr(adapter, "__name__", "adapter")), adapter))

    @property
    def adapter_names(self) -> list[str]:
        with self._lock:
            return [name for name, _ in self._adapters]

    # Create ------------------------------------------------------------------
    def create(
        self,
        title: str,
        *,
        body: str = "",
        severity: str = "info",
        source: str = "runtime",
        kind: str = "message",
        event_id: int | None = None,
        rule_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        severity = normalize_severity(severity)
        if SEVERITY_RANK.get(severity, 1) < SEVERITY_RANK.get(self.min_severity, 1):
            return None
        title = str(title or "").strip() or "DaQauntum notification"
        priority = int(self.config.get("priority_overrides", {}).get(kind, SEVERITY_PRIORITY.get(severity, 30)))
        with self._lock:
            cursor = self.memory.conn.execute(
                """
                INSERT INTO notifications(title, body, severity, priority, status, source, kind, event_id, rule_id, payload_json)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    str(body or ""),
                    severity,
                    priority,
                    str(source or "runtime"),
                    str(kind or "message"),
                    int(event_id) if event_id is not None else None,
                    int(rule_id) if rule_id is not None else None,
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                ),
            )
            notification_id = int(cursor.lastrowid)
            self.memory.conn.execute(
                "DELETE FROM notifications WHERE status = 'dismissed' AND id NOT IN "
                "(SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
                (self.max_notifications,),
            )
            self.memory.conn.commit()
        record = self.get(notification_id) or {}
        self._deliver(record)
        return self.get(notification_id)

    def _deliver(self, record: dict[str, Any]) -> None:
        with self._lock:
            adapters = list(self._adapters)
        delivered: list[str] = []
        for name, adapter in adapters:
            try:
                adapter(dict(record))
                delivered.append(name)
            except Exception as exc:  # pragma: no cover - defensive
                failure = {"adapter": name, "error": f"{type(exc).__name__}: {exc}", "at": time.time()}
                self._delivery_failures.append(failure)
                del self._delivery_failures[:-20]
                try:
                    self.memory.add_event("notification_delivery_failed", failure)
                except Exception:
                    pass
        if delivered and record.get("id"):
            self.memory.conn.execute(
                "UPDATE notifications SET delivered_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(delivered), int(record["id"])),
            )
            self.memory.conn.commit()

    # Reads -------------------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        for field_name, target in (("payload_json", "payload"), ("delivered_json", "delivered")):
            try:
                data[target] = json.loads(data.pop(field_name) or ("{}" if target == "payload" else "[]"))
            except (TypeError, ValueError):
                data[target] = {} if target == "payload" else []
        return data

    def get(self, notification_id: int) -> dict[str, Any] | None:
        row = self.memory.conn.execute(
            "SELECT * FROM notifications WHERE id = ?", (int(notification_id),)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, *, status: str | None = "pending", limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(str(status))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 200)))
        rows = self.memory.conn.execute(
            f"SELECT * FROM notifications {where} ORDER BY priority DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    # Mutations ---------------------------------------------------------------
    def set_status(self, notification_id: int, status: str) -> dict[str, Any] | None:
        status = str(status or "").strip().lower()
        if status not in NOTIFICATION_STATUSES:
            raise ValueError(f"Unknown notification status: {status}")
        self.memory.conn.execute(
            "UPDATE notifications SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, int(notification_id)),
        )
        self.memory.conn.commit()
        return self.get(notification_id)

    def mark_read(self, notification_id: int) -> dict[str, Any] | None:
        return self.set_status(notification_id, "read")

    def dismiss(self, notification_id: int) -> dict[str, Any] | None:
        return self.set_status(notification_id, "dismissed")

    def dismiss_all(self) -> int:
        cursor = self.memory.conn.execute(
            "UPDATE notifications SET status = 'dismissed', updated_at = CURRENT_TIMESTAMP WHERE status != 'dismissed'"
        )
        self.memory.conn.commit()
        return int(cursor.rowcount or 0)

    def stats(self) -> dict[str, Any]:
        rows = self.memory.conn.execute(
            "SELECT status, COUNT(*) AS n FROM notifications GROUP BY status"
        ).fetchall()
        counts = {row["status"]: int(row["n"]) for row in rows}
        unread = self.memory.conn.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE status = 'pending'"
        ).fetchone()
        highest = self.memory.conn.execute(
            "SELECT severity FROM notifications WHERE status = 'pending' ORDER BY priority DESC LIMIT 1"
        ).fetchone()
        return {
            "enabled": self.enabled,
            "pending": int(unread["n"] if unread else 0),
            "by_status": counts,
            "highest_pending_severity": highest["severity"] if highest else None,
            "adapters": self.adapter_names,
            "delivery_failures": list(self._delivery_failures[-5:]),
            "min_severity": self.min_severity,
        }
