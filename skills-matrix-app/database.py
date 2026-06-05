"""SQLite persistence for the skills matrix.

Stores people and their individual question responses. Capability scores
are derived on demand from the stored responses (see ``scoring.py``) so
that a change to weights or the scoring model does not require a data
migration.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(APP_DIR, "data", "responses.db")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thin wrapper around a SQLite database file."""

    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS people (
                    person_id      TEXT PRIMARY KEY,
                    name           TEXT NOT NULL,
                    role           TEXT,
                    location       TEXT,
                    team           TEXT,
                    date_completed TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS responses (
                    response_id       TEXT PRIMARY KEY,
                    person_id         TEXT NOT NULL,
                    question_id       TEXT NOT NULL,
                    capability_area   TEXT NOT NULL,
                    score             INTEGER NOT NULL,
                    free_text_comment TEXT,
                    timestamp         TEXT NOT NULL,
                    FOREIGN KEY (person_id) REFERENCES people (person_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_responses_person
                    ON responses (person_id);
                CREATE INDEX IF NOT EXISTS idx_responses_capability
                    ON responses (capability_area);
                """
            )

    # --- Writes -----------------------------------------------------------

    def save_assessment(
        self,
        name: str,
        role: str,
        location: str,
        team: str,
        responses: Dict[str, dict],
    ) -> str:
        """Persist a completed assessment.

        ``responses`` maps question_id -> {"score": int,
        "capability_area": str, "comment": Optional[str]}.

        Each submission creates a new person record so that the same name
        retaking the assessment keeps a full history rather than silently
        overwriting earlier answers. Returns the new person_id.
        """
        person_id = str(uuid.uuid4())
        completed = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO people
                   (person_id, name, role, location, team, date_completed)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (person_id, name, role, location, team, completed),
            )
            for question_id, payload in responses.items():
                conn.execute(
                    """INSERT INTO responses
                       (response_id, person_id, question_id, capability_area,
                        score, free_text_comment, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        person_id,
                        question_id,
                        payload["capability_area"],
                        int(payload["score"]),
                        payload.get("comment") or None,
                        completed,
                    ),
                )
        return person_id

    def delete_person(self, person_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM people WHERE person_id = ?", (person_id,))

    def import_people(self, people_records: List[dict]) -> int:
        """Bulk-insert previously exported assessments.

        Each record is the per-person structure produced by
        :meth:`export_records`. Existing person_ids are skipped so an
        import is idempotent. Returns the number of people inserted.
        """
        inserted = 0
        with self._connect() as conn:
            for rec in people_records:
                pid = rec.get("person_id") or str(uuid.uuid4())
                exists = conn.execute(
                    "SELECT 1 FROM people WHERE person_id = ?", (pid,)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """INSERT INTO people
                       (person_id, name, role, location, team, date_completed)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        pid,
                        rec.get("name", "Unknown"),
                        rec.get("role"),
                        rec.get("location"),
                        rec.get("team"),
                        rec.get("date_completed") or _now_iso(),
                    ),
                )
                for resp in rec.get("responses", []):
                    conn.execute(
                        """INSERT INTO responses
                           (response_id, person_id, question_id,
                            capability_area, score, free_text_comment, timestamp)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            pid,
                            resp["question_id"],
                            resp["capability_area"],
                            int(resp["score"]),
                            resp.get("comment") or resp.get("free_text_comment"),
                            resp.get("timestamp") or _now_iso(),
                        ),
                    )
                inserted += 1
        return inserted

    # --- Reads ------------------------------------------------------------

    def get_people(self) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM people ORDER BY date_completed DESC", conn
            )

    def get_person(self, person_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM people WHERE person_id = ?", (person_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_responses(self, person_id: Optional[str] = None) -> pd.DataFrame:
        with self._connect() as conn:
            if person_id is None:
                return pd.read_sql_query("SELECT * FROM responses", conn)
            return pd.read_sql_query(
                "SELECT * FROM responses WHERE person_id = ?",
                conn,
                params=(person_id,),
            )

    def export_records(self) -> List[dict]:
        """Return all people with their nested responses, for export."""
        people = self.get_people()
        responses = self.get_responses()
        records = []
        for _, person in people.iterrows():
            person_resp = responses[responses["person_id"] == person["person_id"]]
            records.append(
                {
                    "person_id": person["person_id"],
                    "name": person["name"],
                    "role": person["role"],
                    "location": person["location"],
                    "team": person["team"],
                    "date_completed": person["date_completed"],
                    "responses": [
                        {
                            "question_id": r["question_id"],
                            "capability_area": r["capability_area"],
                            "score": int(r["score"]),
                            "comment": r["free_text_comment"],
                            "timestamp": r["timestamp"],
                        }
                        for _, r in person_resp.iterrows()
                    ],
                }
            )
        return records
