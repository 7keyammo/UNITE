from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


# A candidate's lifecycle. The only transition into "promoted" is an explicit
# human decision recorded with a reason - invariant 12 says autonomous learning
# may prepare a model but may never deploy one.
STATUSES: tuple[str, ...] = ("registered", "evaluated", "promoted", "rejected", "rolled_back", "archived")

TERMINAL_STATUSES = {"rejected", "archived"}


class RegistryError(RuntimeError):
    """Raised for invalid registry transitions."""


class ModelRegistry:
    """Track native model candidates, their evaluations and their promotion.

    The registry is the record of what was trained, on which corpus, how it
    scored, and who decided to deploy it. Without that chain a fine-tuned model
    is an unattributable artifact, and rolling back means guessing.
    """

    def __init__(self, memory, root: str | Path = ".", models_dir: str = "data/native_model/models"):
        self.memory = memory
        self.root = Path(root).resolve()
        self.models_dir = (self.root / models_dir).resolve()
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS native_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                base_model TEXT NOT NULL,
                method TEXT NOT NULL DEFAULT 'lora',
                status TEXT NOT NULL DEFAULT 'registered',
                dataset_manifest TEXT,
                dataset_checksum TEXT,
                artifact_path TEXT,
                hyperparameters_json TEXT NOT NULL DEFAULT '{}',
                evaluation_json TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                evaluated_at REAL,
                promoted_at REAL,
                promoted_by TEXT,
                decision_reason TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS native_model_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                actor TEXT NOT NULL DEFAULT 'human',
                created_at REAL NOT NULL
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_native_models_status ON native_models(status)",
            "CREATE INDEX IF NOT EXISTS idx_native_model_events_candidate ON native_model_events(candidate_id)",
        ):
            self.memory.conn.execute(statement)
        self.memory.conn.commit()

    # Events ------------------------------------------------------------------
    def _log(self, candidate_id: str, event: str, detail: str = "", actor: str = "human") -> None:
        self.memory.conn.execute(
            "INSERT INTO native_model_events(candidate_id, event, detail, actor, created_at) VALUES (?, ?, ?, ?, ?)",
            (candidate_id, event, str(detail)[:1000], actor, time.time()),
        )
        self.memory.conn.commit()

    def history(self, candidate_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if candidate_id:
            rows = self.memory.conn.execute(
                "SELECT * FROM native_model_events WHERE candidate_id = ? ORDER BY id DESC LIMIT ?",
                (candidate_id, int(limit)),
            ).fetchall()
        else:
            rows = self.memory.conn.execute(
                "SELECT * FROM native_model_events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    # Registration ------------------------------------------------------------
    def register(
        self,
        *,
        name: str,
        base_model: str,
        dataset_manifest: str | None = None,
        method: str = "lora",
        hyperparameters: dict[str, Any] | None = None,
        artifact_path: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        base_model = str(base_model or "").strip()
        if not name or not base_model:
            raise RegistryError("A candidate needs both a name and a base model")

        checksum = None
        if dataset_manifest:
            manifest_path = Path(dataset_manifest)
            if not manifest_path.exists():
                raise RegistryError(f"Dataset manifest not found: {dataset_manifest}")
            # Pin the corpus by checksum so a candidate can never be silently
            # re-attributed to a different dataset later.
            checksum = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

        candidate_id = f"cand_{hashlib.sha256(f'{name}{base_model}{time.time()}'.encode()).hexdigest()[:12]}"
        self.memory.conn.execute(
            """
            INSERT INTO native_models(candidate_id, name, base_model, method, status, dataset_manifest,
                                      dataset_checksum, artifact_path, hyperparameters_json, notes, created_at)
            VALUES (?, ?, ?, ?, 'registered', ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id, name, base_model, str(method or "lora"),
                str(dataset_manifest) if dataset_manifest else None, checksum,
                str(artifact_path) if artifact_path else None,
                json.dumps(hyperparameters or {}, ensure_ascii=False, default=str),
                str(notes or ""), time.time(),
            ),
        )
        self.memory.conn.commit()
        self._log(candidate_id, "registered", f"base={base_model} method={method}")
        return self.get(candidate_id) or {}

    # Reads -------------------------------------------------------------------
    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        data = dict(row)
        for source, target in (("hyperparameters_json", "hyperparameters"), ("evaluation_json", "evaluation")):
            raw = data.pop(source, None)
            try:
                data[target] = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                data[target] = None
        return data

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.memory.conn.execute(
            "SELECT * FROM native_models WHERE candidate_id = ?", (str(candidate_id),)
        ).fetchone()
        return self._row(row) if row else None

    def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status:
            rows = self.memory.conn.execute(
                "SELECT * FROM native_models WHERE status = ? ORDER BY id DESC LIMIT ?", (status, int(limit))
            ).fetchall()
        else:
            rows = self.memory.conn.execute(
                "SELECT * FROM native_models ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._row(row) for row in rows]

    def promoted(self) -> dict[str, Any] | None:
        rows = self.list(status="promoted", limit=1)
        return rows[0] if rows else None

    # Transitions -------------------------------------------------------------
    def attach_evaluation(self, candidate_id: str, evaluation: dict[str, Any]) -> dict[str, Any]:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise RegistryError(f"No candidate {candidate_id}")
        if candidate["status"] in TERMINAL_STATUSES:
            raise RegistryError(f"Candidate {candidate_id} is {candidate['status']} and cannot be re-evaluated")
        self.memory.conn.execute(
            "UPDATE native_models SET evaluation_json = ?, evaluated_at = ?, "
            "status = CASE WHEN status = 'registered' THEN 'evaluated' ELSE status END WHERE candidate_id = ?",
            (json.dumps(evaluation, ensure_ascii=False, default=str), time.time(), candidate_id),
        )
        self.memory.conn.commit()
        self._log(candidate_id, "evaluated", f"overall={evaluation.get('overall_score')}", actor="system")
        return self.get(candidate_id) or {}

    def promote(self, candidate_id: str, *, decided_by: str, reason: str, force: bool = False) -> dict[str, Any]:
        """Promote a candidate. Requires a human decision and a passing evaluation.

        `force` exists for the case where an operator knowingly overrides a
        failing gate; it is recorded as an override rather than hidden, because
        the point of the registry is that the decision is attributable.
        """
        candidate = self.get(candidate_id)
        if candidate is None:
            raise RegistryError(f"No candidate {candidate_id}")
        decided_by = str(decided_by or "").strip()
        reason = str(reason or "").strip()
        if not decided_by:
            raise RegistryError("Promotion requires the name of the person deciding")
        if not reason:
            raise RegistryError("Promotion requires a reason, so the decision stays attributable")

        evaluation = candidate.get("evaluation")
        if not evaluation:
            raise RegistryError("Cannot promote a candidate that has not been evaluated")
        if not evaluation.get("passed") and not force:
            raise RegistryError(
                f"Candidate failed evaluation ({evaluation.get('summary', 'no summary')}). "
                "Re-train, or promote with force=True to record a deliberate override."
            )
        if candidate["status"] in TERMINAL_STATUSES:
            raise RegistryError(f"Candidate {candidate_id} is {candidate['status']}")

        now = time.time()
        previous = self.promoted()
        if previous and previous["candidate_id"] != candidate_id:
            # Exactly one model is live at a time; the old one is rolled back
            # explicitly so the registry never shows two promoted candidates.
            self.memory.conn.execute(
                "UPDATE native_models SET status = 'rolled_back' WHERE candidate_id = ?",
                (previous["candidate_id"],),
            )
            self._log(previous["candidate_id"], "rolled_back", f"superseded by {candidate_id}", actor=decided_by)

        self.memory.conn.execute(
            "UPDATE native_models SET status = 'promoted', promoted_at = ?, promoted_by = ?, decision_reason = ? "
            "WHERE candidate_id = ?",
            (now, decided_by, reason + (" [FORCED OVERRIDE]" if force else ""), candidate_id),
        )
        self.memory.conn.commit()
        self._log(candidate_id, "promoted", f"by={decided_by} forced={force}: {reason}", actor=decided_by)
        return self.get(candidate_id) or {}

    def reject(self, candidate_id: str, *, decided_by: str, reason: str) -> dict[str, Any]:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise RegistryError(f"No candidate {candidate_id}")
        self.memory.conn.execute(
            "UPDATE native_models SET status = 'rejected', decision_reason = ? WHERE candidate_id = ?",
            (str(reason or ""), candidate_id),
        )
        self.memory.conn.commit()
        self._log(candidate_id, "rejected", f"by={decided_by}: {reason}", actor=decided_by)
        return self.get(candidate_id) or {}

    def rollback(self, *, decided_by: str, reason: str) -> dict[str, Any] | None:
        """Take the live model out of service. Always available."""
        current = self.promoted()
        if current is None:
            return None
        self.memory.conn.execute(
            "UPDATE native_models SET status = 'rolled_back', decision_reason = ? WHERE candidate_id = ?",
            (str(reason or ""), current["candidate_id"]),
        )
        self.memory.conn.commit()
        self._log(current["candidate_id"], "rolled_back", f"by={decided_by}: {reason}", actor=decided_by)
        return self.get(current["candidate_id"])

    def stats(self) -> dict[str, Any]:
        rows = self.memory.conn.execute(
            "SELECT status, COUNT(*) AS n FROM native_models GROUP BY status"
        ).fetchall()
        live = self.promoted()
        return {
            "candidates": {row["status"]: int(row["n"]) for row in rows},
            "total": sum(int(row["n"]) for row in rows),
            "promoted": {
                "candidate_id": live["candidate_id"],
                "name": live["name"],
                "base_model": live["base_model"],
                "promoted_by": live["promoted_by"],
                "promoted_at": live["promoted_at"],
            } if live else None,
            # Stated explicitly so status output can never imply otherwise.
            "autonomous_promotion": False,
            "models_dir": str(self.models_dir),
        }
