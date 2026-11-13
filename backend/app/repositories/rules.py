from __future__ import annotations

import sqlite3
from typing import Any

from app.modules.shadow_rule_pack import TrackError, normalize_track


def list_rules(conn: sqlite3.Connection, dataset_id: int | None = None) -> list[dict[str, Any]]:
    cols = "id, dataset_id, name, field, rule_type, severity, params_json, enabled, track"
    if dataset_id is None:
        rows = conn.execute(f"SELECT {cols} FROM rules ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            f"SELECT {cols} FROM rules WHERE dataset_id=? ORDER BY id",
            (dataset_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_rule(conn: sqlite3.Connection, rule_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, dataset_id, name, field, rule_type, severity, params_json, enabled, track "
        "FROM rules WHERE id=?",
        (rule_id,),
    ).fetchone()
    return dict(row) if row else None


def set_enabled(conn: sqlite3.Connection, rule_id: int, enabled: bool) -> None:
    conn.execute("UPDATE rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))


def set_track(conn: sqlite3.Connection, rule_id: int, track: str) -> None:
    """切换主/影子归属。track 为单值列 + CHECK 约束，一条规则不可能同时属两边。"""
    track = normalize_track(track)
    cur = conn.execute("UPDATE rules SET track=? WHERE id=?", (track, rule_id))
    if cur.rowcount == 0:
        raise TrackError(f"rule {rule_id} 不存在")
