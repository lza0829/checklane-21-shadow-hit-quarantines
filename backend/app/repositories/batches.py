from __future__ import annotations

import json
import sqlite3
from typing import Any

_BATCH_COLS = (
    "id, dataset_id, status, scanned_count, passed_count, failed_count, "
    "quarantined_count, released_count, report_json, main_rules_json, shadow_rules_json, created_at"
)


def _hydrate_batch(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["report"] = json.loads(d.pop("report_json"))
    d["main_rules"] = json.loads(d.pop("main_rules_json"))
    d["shadow_rules"] = json.loads(d.pop("shadow_rules_json"))
    return d


def insert_batch(
    conn: sqlite3.Connection,
    *,
    dataset_id: int,
    status: str,
    scanned: int,
    passed: int,
    failed: int,
    quarantined: int,
    released: int,
    report: dict[str, Any],
    main_rules: list[dict[str, Any]],
    shadow_rules: list[dict[str, Any]],
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO inspect_batches(
          dataset_id, status, scanned_count, passed_count, failed_count,
          quarantined_count, released_count, report_json, main_rules_json,
          shadow_rules_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            dataset_id,
            status,
            scanned,
            passed,
            failed,
            quarantined,
            released,
            json.dumps(report, ensure_ascii=False),
            json.dumps(main_rules, ensure_ascii=False),
            json.dumps(shadow_rules, ensure_ascii=False),
            created_at,
        ),
    )
    return int(cur.lastrowid)


def update_report(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    released: int,
    report: dict[str, Any],
    status: str,
) -> None:
    conn.execute(
        """
        UPDATE inspect_batches
        SET released_count=?, report_json=?, status=?
        WHERE id=?
        """,
        (released, json.dumps(report, ensure_ascii=False), status, batch_id),
    )


def insert_row(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    row_no: int,
    entity_key: str | None,
    payload: dict[str, Any],
    disposition: str,
    rule_hits: list[dict[str, Any]],
    shadow_hits: list[dict[str, Any]] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO inspect_rows(
          batch_id, row_no, entity_key, payload_json, disposition,
          rule_hits_json, shadow_hits_json
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            row_no,
            entity_key,
            json.dumps(payload, ensure_ascii=False),
            disposition,
            json.dumps(rule_hits, ensure_ascii=False),
            json.dumps(shadow_hits or [], ensure_ascii=False),
        ),
    )


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_BATCH_COLS} FROM inspect_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_hydrate_batch(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {_BATCH_COLS} FROM inspect_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    return _hydrate_batch(row) if row else None


def list_batch_rows(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, batch_id, row_no, entity_key, payload_json, disposition,
               rule_hits_json, shadow_hits_json
        FROM inspect_rows WHERE batch_id=? ORDER BY row_no
        """,
        (batch_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        d["rule_hits"] = json.loads(d.pop("rule_hits_json"))
        d["shadow_hits"] = json.loads(d.pop("shadow_hits_json"))
        out.append(d)
    return out
