from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class NativeDatasetBuilder:
    """Builds an auditable seed dataset without changing model weights."""

    def __init__(self, memory, project_root: str = ".", output_dir: str = "data/native_model/datasets"):
        self.memory = memory
        self.root = Path(project_root).resolve()
        self.output_dir = (self.root / output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def stats(self) -> dict[str, Any]:
        training = int(self.memory.conn.execute("SELECT COUNT(*) FROM memory_items WHERE active=1 AND kind='training'").fetchone()[0])
        approved_learning = int(self.memory.conn.execute("SELECT COUNT(*) FROM learning_sessions WHERE status='completed' AND critic_approved=1").fetchone()[0]) if self._table_exists("learning_sessions") else 0
        exports = sorted(self.output_dir.glob("*.jsonl"))
        return {"training_memories": training, "approved_learning_sessions": approved_learning, "exports": len(exports), "latest_export": str(exports[-1].relative_to(self.root)) if exports else None, "fine_tuning_enabled": False}

    def export(self, path: str | None = None, limit: int = 5000) -> dict[str, Any]:
        target = Path(path).resolve() if path else self.output_dir / f"daqauntum-seed-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
        if self.root not in target.parents and target != self.root:
            raise ValueError("Native dataset path must remain inside the DaQauntum project root")
        rows = self.memory.conn.execute(
            "SELECT * FROM memory_items WHERE active=1 AND kind='training' ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in reversed(rows):
            content = str(row["content"])
            parsed = self._parse_training_memory(content)
            if not parsed:
                continue
            key = parsed["user"].strip() + "\n" + parsed["assistant"].strip()
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "messages": [
                    {"role": "system", "content": "You are DaQauntum. Be accurate, evidence-aware, privacy-conscious, and explicit about uncertainty."},
                    {"role": "user", "content": parsed["user"]},
                    {"role": "assistant", "content": parsed["assistant"]},
                ],
                "metadata": {"memory_id": int(row["id"]), "confidence": float(row["confidence"]), "project": row["project"], "source": row["source"]},
            })
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"path": str(target), "records": len(records), "fine_tuning_performed": False}

    @staticmethod
    def _parse_training_memory(content: str) -> dict[str, str] | None:
        match = re.search(r"Request:\s*(.*?)\nPlan:\s*(.*?)\nResponse:\s*(.*)$", content, flags=re.S)
        if not match:
            return None
        return {"user": match.group(1).strip(), "plan": match.group(2).strip(), "assistant": match.group(3).strip()}

    def _table_exists(self, name: str) -> bool:
        row = self.memory.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        return bool(row)
