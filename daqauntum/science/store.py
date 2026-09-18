"""Persistence for the scientific core.

Follows the convention every other DaQauntum subsystem uses: tables created
with CREATE TABLE IF NOT EXISTS on the one shared SQLite connection owned by
`memory/store.py`. No second database, no ORM, no migration framework - so a
scientific record lives in the same file and the same backup as everything else
DaQauntum knows.

Objects are stored as typed columns for the fields that get queried and a JSON
blob for the rest. That keeps lookups cheap without freezing the schema every
time a model gains a field.
"""
from __future__ import annotations

import json
from typing import Any

from science.models import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceKind,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    Measurement,
    now,
)


class ScienceStore:
    """Durable storage for experiments and everything under them."""

    def __init__(self, memory):
        self.memory = memory
        self.conn = memory.conn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                research_question TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                depth TEXT NOT NULL DEFAULT 'intermediate',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                document_json TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hypotheses (
                id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                statement TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                document_json TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS measurements (
                id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                quantity TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL DEFAULT '',
                uncertainty REAL,
                derived INTEGER NOT NULL DEFAULT 0,
                timestamp REAL NOT NULL,
                document_json TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                statement TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '',
                value REAL,
                unit TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                document_json TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                statement TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                created_at REAL NOT NULL,
                document_json TEXT NOT NULL
            )
            """
        )
        # Additive migration: older databases predate the provenance column.
        # Filtering on provenance is how a caller asks for readings of the world
        # only, so it has to be a column rather than a scan of document_json.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(measurements)")}
        if "provenance_kind" not in columns:
            self.conn.execute(
                "ALTER TABLE measurements ADD COLUMN provenance_kind TEXT NOT NULL DEFAULT 'human'"
            )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_hypotheses_experiment ON hypotheses(experiment_id)",
            "CREATE INDEX IF NOT EXISTS idx_measurements_experiment ON measurements(experiment_id)",
            "CREATE INDEX IF NOT EXISTS idx_measurements_quantity ON measurements(experiment_id, quantity)",
            "CREATE INDEX IF NOT EXISTS idx_measurements_run ON measurements(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_measurements_provenance ON measurements(experiment_id, provenance_kind)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_experiment ON evidence(experiment_id)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_kind ON evidence(kind)",
            "CREATE INDEX IF NOT EXISTS idx_claims_experiment ON claims(experiment_id)",
        ):
            self.conn.execute(statement)
        self.conn.commit()

    # Experiments -------------------------------------------------------------
    def save_experiment(self, experiment: Experiment) -> Experiment:
        experiment.updated_at = now()
        self.conn.execute(
            """
            INSERT INTO experiments(id, title, research_question, status, depth,
                                    created_at, updated_at, document_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, research_question=excluded.research_question,
                status=excluded.status, depth=excluded.depth,
                updated_at=excluded.updated_at, document_json=excluded.document_json
            """,
            (experiment.id, experiment.title, experiment.research_question,
             experiment.status.value, experiment.depth.value,
             experiment.created_at, experiment.updated_at,
             json.dumps(experiment.as_dict(), ensure_ascii=False, default=str)),
        )
        self.conn.commit()
        return experiment

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        row = self.conn.execute(
            "SELECT document_json FROM experiments WHERE id = ?", (str(experiment_id),)
        ).fetchone()
        return Experiment.from_dict(json.loads(row["document_json"])) if row else None

    def list_experiments(self, *, limit: int = 50, status: str | None = None) -> list[Experiment]:
        if status:
            rows = self.conn.execute(
                "SELECT document_json FROM experiments WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status, max(1, min(int(limit), 500))),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT document_json FROM experiments ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [Experiment.from_dict(json.loads(row["document_json"])) for row in rows]

    def set_experiment_status(self, experiment_id: str, status: ExperimentStatus) -> Experiment | None:
        experiment = self.get_experiment(experiment_id)
        if experiment is None:
            return None
        experiment.status = status if isinstance(status, ExperimentStatus) else ExperimentStatus(str(status))
        return self.save_experiment(experiment)

    # Hypotheses --------------------------------------------------------------
    def save_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        hypothesis.updated_at = now()
        self.conn.execute(
            """
            INSERT INTO hypotheses(id, experiment_id, statement, status, created_at, updated_at, document_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                statement=excluded.statement, status=excluded.status,
                updated_at=excluded.updated_at, document_json=excluded.document_json
            """,
            (hypothesis.id, hypothesis.experiment_id, hypothesis.statement,
             hypothesis.status.value, hypothesis.created_at, hypothesis.updated_at,
             json.dumps(hypothesis.as_dict(), ensure_ascii=False, default=str)),
        )
        self.conn.commit()
        return hypothesis

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        row = self.conn.execute(
            "SELECT document_json FROM hypotheses WHERE id = ?", (str(hypothesis_id),)
        ).fetchone()
        return Hypothesis.from_dict(json.loads(row["document_json"])) if row else None

    def list_hypotheses(self, experiment_id: str) -> list[Hypothesis]:
        rows = self.conn.execute(
            "SELECT document_json FROM hypotheses WHERE experiment_id = ? ORDER BY created_at ASC",
            (str(experiment_id),),
        ).fetchall()
        return [Hypothesis.from_dict(json.loads(row["document_json"])) for row in rows]

    # Measurements ------------------------------------------------------------
    def save_measurement(self, measurement: Measurement) -> Measurement:
        self.conn.execute(
            """
            INSERT INTO measurements(id, experiment_id, run_id, quantity, value, unit,
                                     uncertainty, derived, timestamp, provenance_kind,
                                     document_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value=excluded.value, unit=excluded.unit, uncertainty=excluded.uncertainty,
                document_json=excluded.document_json
            """,
            (measurement.id, measurement.experiment_id, measurement.run_id, measurement.quantity,
             measurement.value, measurement.unit, measurement.uncertainty,
             1 if measurement.derived else 0, measurement.timestamp,
             measurement.provenance.kind.value,
             json.dumps(measurement.as_dict(), ensure_ascii=False, default=str)),
        )
        self.conn.commit()
        return measurement

    def list_measurements(
        self,
        experiment_id: str,
        *,
        quantity: str | None = None,
        run_id: str | None = None,
        include_derived: bool = True,
        include_simulated: bool = True,
        limit: int = 10_000,
    ) -> list[Measurement]:
        """Measurements in time order.

        `include_derived=False` is how an analysis asks for raw readings only.
        Without it, a second analysis pass would happily consume its own output.

        `include_simulated=False` additionally excludes values a simulator
        produced. Both default to True so a caller sees the whole record; a
        caller that needs only observations of the world says so explicitly.
        """
        clauses = ["experiment_id = ?"]
        params: list[Any] = [str(experiment_id)]
        if quantity:
            clauses.append("quantity = ?")
            params.append(quantity)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if not include_derived:
            clauses.append("derived = 0")
        if not include_simulated:
            clauses.append("provenance_kind != 'simulated'")
        params.append(max(1, min(int(limit), 100_000)))
        rows = self.conn.execute(
            f"SELECT document_json FROM measurements WHERE {' AND '.join(clauses)} "
            f"ORDER BY timestamp ASC, id ASC LIMIT ?",
            params,
        ).fetchall()
        return [Measurement.from_dict(json.loads(row["document_json"])) for row in rows]

    def get_measurement(self, measurement_id: str) -> Measurement | None:
        row = self.conn.execute(
            "SELECT document_json FROM measurements WHERE id = ?", (str(measurement_id),)
        ).fetchone()
        return Measurement.from_dict(json.loads(row["document_json"])) if row else None

    # Evidence ----------------------------------------------------------------
    def save_evidence(self, evidence: Evidence) -> Evidence:
        self.conn.execute(
            """
            INSERT INTO evidence(id, experiment_id, kind, statement, quantity, value, unit,
                                 created_at, document_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                statement=excluded.statement, document_json=excluded.document_json
            """,
            (evidence.id, evidence.experiment_id, evidence.kind.value, evidence.statement,
             evidence.quantity, evidence.value, evidence.unit, evidence.created_at,
             json.dumps(evidence.as_dict(), ensure_ascii=False, default=str)),
        )
        self.conn.commit()
        return evidence

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        row = self.conn.execute(
            "SELECT document_json FROM evidence WHERE id = ?", (str(evidence_id),)
        ).fetchone()
        return Evidence.from_dict(json.loads(row["document_json"])) if row else None

    def list_evidence(
        self, experiment_id: str, *, kind: EvidenceKind | str | None = None, limit: int = 500
    ) -> list[Evidence]:
        clauses = ["experiment_id = ?"]
        params: list[Any] = [str(experiment_id)]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value if isinstance(kind, EvidenceKind) else str(kind))
        params.append(max(1, min(int(limit), 5000)))
        rows = self.conn.execute(
            f"SELECT document_json FROM evidence WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at ASC LIMIT ?",
            params,
        ).fetchall()
        return [Evidence.from_dict(json.loads(row["document_json"])) for row in rows]

    # Claims ------------------------------------------------------------------
    def save_claim(self, claim: Claim) -> Claim:
        self.conn.execute(
            """
            INSERT INTO claims(id, experiment_id, statement, status, created_at, document_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                statement=excluded.statement, status=excluded.status,
                document_json=excluded.document_json
            """,
            (claim.id, claim.experiment_id, claim.statement, claim.status.value,
             claim.created_at, json.dumps(claim.as_dict(), ensure_ascii=False, default=str)),
        )
        self.conn.commit()
        return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        row = self.conn.execute(
            "SELECT document_json FROM claims WHERE id = ?", (str(claim_id),)
        ).fetchone()
        return Claim.from_dict(json.loads(row["document_json"])) if row else None

    def list_claims(self, experiment_id: str) -> list[Claim]:
        rows = self.conn.execute(
            "SELECT document_json FROM claims WHERE experiment_id = ? ORDER BY created_at ASC",
            (str(experiment_id),),
        ).fetchall()
        return [Claim.from_dict(json.loads(row["document_json"])) for row in rows]

    # Aggregate ---------------------------------------------------------------
    def experiment_record(self, experiment_id: str) -> dict[str, Any] | None:
        """Everything belonging to one experiment, for display or export."""
        experiment = self.get_experiment(experiment_id)
        if experiment is None:
            return None
        evidence = self.list_evidence(experiment_id)
        return {
            "experiment": experiment.as_dict(),
            "hypotheses": [h.as_dict() for h in self.list_hypotheses(experiment_id)],
            "measurements": [m.as_dict() for m in self.list_measurements(experiment_id)],
            "evidence": [e.as_dict() for e in evidence],
            "claims": [c.as_dict() for c in self.list_claims(experiment_id)],
            # A summary by kind, so a reader can see at a glance how much of an
            # experiment is measured and how much is inferred.
            "evidence_by_kind": {
                kind.value: sum(1 for e in evidence if e.kind is kind)
                for kind in EvidenceKind
                if any(e.kind is kind for e in evidence)
            },
        }

    def stats(self) -> dict[str, Any]:
        def count(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        by_kind = self.conn.execute(
            "SELECT kind, COUNT(*) AS n FROM evidence GROUP BY kind"
        ).fetchall()
        raw = int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM measurements "
            "WHERE derived = 0 AND provenance_kind != 'simulated'"
        ).fetchone()["n"])
        simulated = int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM measurements WHERE provenance_kind = 'simulated'"
        ).fetchone()["n"])
        return {
            "experiments": count("experiments"),
            "hypotheses": count("hypotheses"),
            "measurements": count("measurements"),
            "raw_measurements": raw,
            "simulated_measurements": simulated,
            "derived_measurements": count("measurements") - raw - simulated,
            "evidence": count("evidence"),
            "evidence_by_kind": {row["kind"]: int(row["n"]) for row in by_kind},
            "claims": count("claims"),
        }
