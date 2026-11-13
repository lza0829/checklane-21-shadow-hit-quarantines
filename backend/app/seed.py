from datetime import datetime, timezone

from app.config import DATA_DIR
from app.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn) -> None:
    """老库补列/补表，不改动任何已结束批次的数据。"""
    rule_cols = _columns(conn, "rules")
    if "track" not in rule_cols:
        # ALTER TABLE 加列不会带 CHECK，约束由应用层 normalize_track 兜底；
        # 这里再做一次存量数据校验，防止库内出现 main/shadow 之外的归属。
        conn.execute("ALTER TABLE rules ADD COLUMN track TEXT NOT NULL DEFAULT 'main'")
    bad = conn.execute(
        "SELECT COUNT(*) AS c FROM rules WHERE track NOT IN ('main','shadow')"
    ).fetchone()["c"]
    if bad:
        raise RuntimeError(f"rules 表存在 {bad} 条非法 track 归属（只允许 main/shadow）")
    row_cols = _columns(conn, "inspect_rows")
    if "shadow_hits_json" not in row_cols:
        conn.execute("ALTER TABLE inspect_rows ADD COLUMN shadow_hits_json TEXT NOT NULL DEFAULT '[]'")
    batch_cols = _columns(conn, "inspect_batches")
    if "main_rules_json" not in batch_cols:
        conn.execute("ALTER TABLE inspect_batches ADD COLUMN main_rules_json TEXT NOT NULL DEFAULT '[]'")
    if "shadow_rules_json" not in batch_cols:
        conn.execute("ALTER TABLE inspect_batches ADD COLUMN shadow_rules_json TEXT NOT NULL DEFAULT '[]'")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_hits(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          rule_id INTEGER NOT NULL,
          rule_name TEXT NOT NULL,
          field TEXT NOT NULL,
          rule_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          detail TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(batch_id) REFERENCES inspect_batches(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_hits_batch ON shadow_hits(batch_id, row_no)"
    )


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS datasets(
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          fixture_path TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          note TEXT
        );
        CREATE TABLE IF NOT EXISTS rules(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          field TEXT NOT NULL,
          rule_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          params_json TEXT NOT NULL DEFAULT '{}',
          enabled INTEGER NOT NULL DEFAULT 1,
          track TEXT NOT NULL DEFAULT 'main' CHECK(track IN ('main','shadow')),
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS inspect_batches(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          scanned_count INTEGER NOT NULL DEFAULT 0,
          passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          quarantined_count INTEGER NOT NULL DEFAULT 0,
          released_count INTEGER NOT NULL DEFAULT 0,
          report_json TEXT NOT NULL,
          main_rules_json TEXT NOT NULL DEFAULT '[]',
          shadow_rules_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS inspect_rows(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          payload_json TEXT NOT NULL,
          disposition TEXT NOT NULL,
          rule_hits_json TEXT NOT NULL,
          shadow_hits_json TEXT NOT NULL DEFAULT '[]',
          FOREIGN KEY(batch_id) REFERENCES inspect_batches(id)
        );
        CREATE TABLE IF NOT EXISTS shadow_hits(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          rule_id INTEGER NOT NULL,
          rule_name TEXT NOT NULL,
          field TEXT NOT NULL,
          rule_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          detail TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(batch_id) REFERENCES inspect_batches(id)
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_hits_batch ON shadow_hits(batch_id, row_no);
        CREATE TABLE IF NOT EXISTS quarantine(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          dataset_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          reason TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS curated(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          batch_id INTEGER NOT NULL,
          entity_key TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          released_at TEXT NOT NULL,
          UNIQUE(dataset_id, entity_key)
        );
        """
    )
    _migrate(conn)
    n = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
    if n == 0:
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            ("Orders Snapshot", "orders_snapshot.jsonl", "order", 1, "含缺失与类型错误行，演示入检"),
        )
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            ("Inventory Snapshot", "inventory_snapshot.jsonl", "sku", 1, "库存快照第二路"),
        )
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            (
                "Shadow Rule Pack Demo",
                "shadow_demo.jsonl",
                "order",
                1,
                "主/影子双轨对照：主过影子多拦一行 + 主隔离影子未中一行",
            ),
        )
        conn.executemany(
            "INSERT INTO rules(dataset_id, name, field, rule_type, severity, params_json, enabled, track) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                # Orders — 纯主集（影子集关闭，等价单主集入检）
                (1, "sku 必填", "sku", "required", "error", "{}", 1, "main"),
                (1, "price 须为数字", "price", "type_number", "error", "{}", 1, "main"),
                (1, "qty 须为整数", "qty", "type_int", "warn", "{}", 1, "main"),
                # Inventory — 纯主集
                (2, "sku 必填", "sku", "required", "error", "{}", 1, "main"),
                (2, "on_hand 须为整数", "on_hand", "type_int", "error", "{}", 1, "main"),
                # Shadow demo — 主集两条 + 影子集一条（暗跑观察，不参与处置；
                # error 级才能验证“影子多拦也不许进隔离/减签发”）
                (3, "sku 必填", "sku", "required", "error", "{}", 1, "main"),
                (3, "price 须为数字", "price", "type_number", "error", "{}", 1, "main"),
                (3, "tag 必填（影子暗跑）", "tag", "required", "error", "{}", 1, "shadow"),
            ],
        )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            ("release_mode", "pass_only"),
        )
    conn.commit()
    conn.close()
