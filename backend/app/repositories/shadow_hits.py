from __future__ import annotations

import sqlite3
from typing import Any

# 行级影子命中只观察、不处置：独立于 quarantine / curated 持久化。
_INSERT_COLS = (
    "batch_id, row_no, entity_key, rule_id, rule_name, field, "
    "rule_type, severity, detail, created_at"
)


def insert_many(conn: sqlite3.Connection, records: list[tuple[Any, ...]]) -> None:
    if not records:
        return
    placeholders = ",".join("?" for _ in _INSERT_COLS.split(", "))
    conn.executemany(
        f"INSERT INTO shadow_hits({_INSERT_COLS}) VALUES ({placeholders})",
        records,
    )


def list_by_batch(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, batch_id, row_no, entity_key, rule_id, rule_name, field,
               rule_type, severity, detail, created_at
        FROM shadow_hits WHERE batch_id=? ORDER BY row_no, id
        """,
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_by_batch(conn: sqlite3.Connection, batch_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM shadow_hits WHERE batch_id=?", (batch_id,)
        ).fetchone()["c"]
    )


def count_all(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM shadow_hits").fetchone()["c"])
