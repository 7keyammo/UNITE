from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from events.models import Event, PublishResult, normalize_severity, severity_at_least


Subscriber = Callable[[Event], Any]


class EventBus:
    """One normalized pipeline for every approved observation.

    Responsibilities are deliberately narrow:

    * normalize and persist observations as an audit trail;
    * suppress repeats so noisy sources cannot spam the user or re-trigger
      reactions (deduplication, debounce and per-kind cooldown);
    * hand accepted events to subscribers.

    The bus never performs an action of its own and never gives a subscriber
    any authority it did not already have. A source that can publish an event
    has not thereby gained the ability to change anything.
    """

    def __init__(self, memory, config: dict[str, Any] | None = None):
        self.memory = memory
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        self.debounce_seconds = max(0.0, float(self.config.get("debounce_seconds", 30)))
        self.cooldown_seconds = max(0.0, float(self.config.get("cooldown_seconds", 0)))
        self.min_severity = normalize_severity(self.config.get("min_severity", "debug"), "debug")
        self.max_events = max(100, int(self.config.get("max_events", 5000)))
        self.per_kind: dict[str, dict[str, Any]] = dict(self.config.get("per_kind", {}) or {})
        self._subscribers: list[tuple[str, Subscriber]] = []
        self._lock = threading.RLock()
        self._degraded: list[dict[str, Any]] = []
        self._ensure_schema()

    # Schema ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT 'default',
                severity TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL DEFAULT '',
                attributes_json TEXT NOT NULL DEFAULT '{}',
                dedupe_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL DEFAULT '',
                occurred_at REAL NOT NULL DEFAULT 0,
                correlation_id TEXT,
                repeat_count INTEGER NOT NULL DEFAULT 0,
                last_repeat_at REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Additive migration: databases created before v0.5 have no
        # correlation_id column, and there is no migration framework.
        existing = {row[1] for row in self.memory.conn.execute("PRAGMA table_info(device_events)")}
        if "correlation_id" not in existing:
            self.memory.conn.execute("ALTER TABLE device_events ADD COLUMN correlation_id TEXT")
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_device_events_id ON device_events(id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_device_events_correlation ON device_events(correlation_id)",
            "CREATE INDEX IF NOT EXISTS idx_device_events_dedupe ON device_events(dedupe_key, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_device_events_source ON device_events(source)",
            "CREATE INDEX IF NOT EXISTS idx_device_events_kind ON device_events(kind)",
            "CREATE INDEX IF NOT EXISTS idx_device_events_severity ON device_events(severity)",
        ):
            self.memory.conn.execute(statement)
        self.memory.conn.commit()

    # Subscribers -------------------------------------------------------------
    def subscribe(self, callback: Subscriber, *, name: str | None = None) -> Subscriber:
        """Register an observer of accepted events.

        Subscribers are advisory. A subscriber that wants to change state must
        route through the tool registry and permission manager exactly as any
        other caller would.
        """
        if not callable(callback):
            raise TypeError("Event subscriber must be callable")
        label = str(name or getattr(callback, "__name__", "subscriber"))
        with self._lock:
            self._subscribers.append((label, callback))
        return callback

    def unsubscribe(self, callback: Subscriber) -> bool:
        with self._lock:
            for index, (_, existing) in enumerate(self._subscribers):
                if existing is callback:
                    self._subscribers.pop(index)
                    return True
        return False

    @property
    def subscriber_names(self) -> list[str]:
        with self._lock:
            return [name for name, _ in self._subscribers]

    # Policy ------------------------------------------------------------------
    def _policy_for(self, event: Event) -> dict[str, Any]:
        """Resolve suppression policy, most specific match first."""
        merged = {
            "debounce_seconds": self.debounce_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "min_severity": self.min_severity,
        }
        for key in (f"{event.source}:{event.kind}", event.kind, event.source):
            override = self.per_kind.get(key)
            if isinstance(override, dict):
                merged.update({k: v for k, v in override.items() if v is not None})
                break
        return merged

    def _last_accepted(self, dedupe_key: str) -> dict[str, Any] | None:
        row = self.memory.conn.execute(
            "SELECT id, fingerprint, occurred_at, repeat_count FROM device_events "
            "WHERE dedupe_key = ? ORDER BY id DESC LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        return dict(row) if row else None

    def _suppression_reason(self, event: Event, policy: dict[str, Any]) -> str | None:
        """Decide deterministically whether this observation is news.

        Returns a reason string when the event should be suppressed, else None.
        """
        if not severity_at_least(event.severity, policy.get("min_severity", "debug")):
            return "below-min-severity"

        previous = self._last_accepted(event.dedupe_key or "")
        if not previous:
            return None

        elapsed = event.occurred_at - float(previous.get("occurred_at") or 0.0)
        if elapsed < 0:
            # An out-of-order sample is not newer information.
            return "out-of-order"

        same_state = previous.get("fingerprint") == event.state_fingerprint
        debounce = max(0.0, float(policy.get("debounce_seconds", 0) or 0))
        cooldown = max(0.0, float(policy.get("cooldown_seconds", 0) or 0))

        # Deduplication/debounce: identical state repeated inside the window.
        if same_state and elapsed < debounce:
            return "duplicate-within-debounce"

        # Cooldown applies even when the value changed, so a sensor oscillating
        # around a threshold cannot fire a reaction repeatedly.
        if elapsed < cooldown:
            return "within-cooldown"

        # Outside every window an unchanged state is still not news unless the
        # policy explicitly asks for heartbeats.
        if same_state and not bool(policy.get("repeat_unchanged", False)):
            return "unchanged-state"

        return None

    # Publish -----------------------------------------------------------------
    def publish(self, event: Event | None = None, **kwargs: Any) -> PublishResult:
        """Offer an observation to the bus.

        Accepts either a prepared Event or the keyword fields of one. The
        result always describes what happened, including why an event was
        suppressed, so callers can report degraded behavior instead of guessing.
        """
        if event is None:
            event = Event(**kwargs)
        elif kwargs:
            raise TypeError("Pass either an Event or its fields, not both")

        if not self.enabled:
            return PublishResult(event=event, accepted=False, reason="event-bus-disabled")

        with self._lock:
            policy = self._policy_for(event)
            reason = self._suppression_reason(event, policy)
            if reason is not None:
                self._record_repeat(event)
                return PublishResult(event=event, accepted=False, reason=reason)
            stored = self._store(event)

        # Dispatch happens outside the lock so a slow subscriber cannot block
        # other publishers.
        self._dispatch(stored)
        return PublishResult(event=stored, accepted=True, reason="accepted", event_id=stored.event_id)

    def _store(self, event: Event) -> Event:
        cursor = self.memory.conn.execute(
            """
            INSERT INTO device_events(
                source, kind, subject, severity, message,
                attributes_json, dedupe_key, fingerprint, occurred_at, correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.source,
                event.kind,
                event.subject,
                event.severity,
                event.message,
                json.dumps(event.attributes, ensure_ascii=False, default=str),
                event.dedupe_key,
                event.state_fingerprint,
                event.occurred_at,
                event.correlation_id,
            ),
        )
        self.memory.conn.execute(
            "DELETE FROM device_events WHERE id NOT IN "
            "(SELECT id FROM device_events ORDER BY id DESC LIMIT ?)",
            (self.max_events,),
        )
        self.memory.conn.commit()
        return event.with_id(int(cursor.lastrowid))

    def _record_repeat(self, event: Event) -> None:
        """Keep suppressed observations visible without growing the log.

        Storing every duplicate would let a 1 Hz sensor evict the audit trail,
        so a repeat increments a counter on the last accepted event instead.
        """
        self.memory.conn.execute(
            """
            UPDATE device_events
               SET repeat_count = repeat_count + 1, last_repeat_at = ?
             WHERE id = (SELECT id FROM device_events WHERE dedupe_key = ? ORDER BY id DESC LIMIT 1)
            """,
            (event.occurred_at, event.dedupe_key),
        )
        self.memory.conn.commit()

    def _dispatch(self, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for name, callback in subscribers:
            try:
                callback(event)
            except Exception as exc:  # pragma: no cover - defensive
                # A failing subscriber degrades reaction coverage. Record it
                # rather than swallowing it, so status output stays honest.
                self._note_degraded(name, exc)

    def _note_degraded(self, subscriber: str, exc: Exception) -> None:
        entry = {
            "subscriber": subscriber,
            "error": f"{type(exc).__name__}: {exc}",
            "at": time.time(),
        }
        self._degraded.append(entry)
        del self._degraded[:-20]
        try:
            self.memory.add_event("event_subscriber_failed", entry)
        except Exception:  # pragma: no cover - the store itself is unavailable
            pass

    # Reads -------------------------------------------------------------------
    def recent(
        self,
        limit: int = 50,
        *,
        source: str | None = None,
        kind: str | None = None,
        min_severity: str | None = None,
        since_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if source:
            clauses.append("source = ?")
            params.append(str(source).strip().lower())
        if kind:
            clauses.append("kind = ?")
            params.append(str(kind).strip().lower())
        if since_id is not None:
            clauses.append("id > ?")
            params.append(int(since_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        rows = self.memory.conn.execute(
            f"SELECT * FROM device_events {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        events = []
        for row in rows:
            data = dict(row)
            if min_severity and not severity_at_least(data.get("severity", "info"), min_severity):
                continue
            payload = Event.from_row(row).as_dict()
            payload["repeat_count"] = int(data.get("repeat_count") or 0)
            payload["last_repeat_at"] = data.get("last_repeat_at")
            payload["created_at"] = data.get("created_at")
            events.append(payload)
        return events

    def stats(self) -> dict[str, Any]:
        total = self.memory.conn.execute("SELECT COUNT(*) AS n FROM device_events").fetchone()
        repeats = self.memory.conn.execute(
            "SELECT COALESCE(SUM(repeat_count), 0) AS n FROM device_events"
        ).fetchone()
        by_source = self.memory.conn.execute(
            "SELECT source, COUNT(*) AS n FROM device_events GROUP BY source ORDER BY n DESC LIMIT 12"
        ).fetchall()
        by_severity = self.memory.conn.execute(
            "SELECT severity, COUNT(*) AS n FROM device_events GROUP BY severity"
        ).fetchall()
        return {
            "enabled": self.enabled,
            "events": int(total["n"] if total else 0),
            "suppressed_repeats": int(repeats["n"] if repeats else 0),
            "retention_limit": self.max_events,
            "debounce_seconds": self.debounce_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "min_severity": self.min_severity,
            "subscribers": self.subscriber_names,
            "by_source": {row["source"]: int(row["n"]) for row in by_source},
            "by_severity": {row["severity"]: int(row["n"]) for row in by_severity},
            "degraded": list(self._degraded[-5:]),
        }
