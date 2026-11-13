import importlib

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.db
    import app.seed
    import app.services.inspect_service as svc

    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.seed)
    importlib.reload(svc)
    app.seed.init_db()
    return svc


def test_orders_inspect_quarantine_and_release(env):
    detail = env.run_inspect(1)
    assert detail["scanned_count"] == 6
    # ORD-3 missing sku, ORD-4 bad price → quarantine; ORD-5 warn-only still pass
    assert detail["quarantined_count"] == 2
    assert detail["passed_count"] == 4
    assert detail["released_count"] == 4
    assert all(c["ok"] for c in detail["report"]["checks"] if c["name"] == "scan_breakdown")
    assert all(c["ok"] for c in detail["report"]["checks"] if c["name"] == "release_matches_passed")


# ---- 影子规则包：主/影子双轨 ----

SHADOW_DATASET = 3


def _check(detail, name):
    hits = [c for c in detail["report"]["checks"] if c["name"] == name]
    assert hits, f"缺少对账项 {name}"
    assert all(c["ok"] for c in hits), f"对账项 {name} 未通过: {hits}"


def test_shadow_pack_dual_track_contrast_fixture(env):
    """同一夹具两条对照：主过影子中 + 主中影子过，外加两侧都中/都过。"""
    detail = env.run_inspect(SHADOW_DATASET)

    # 两套规则都参与评估，批次回看得到两套清单，且互斥
    assert [r["id"] for r in detail["main_rules"]] == [6, 7]
    assert [r["id"] for r in detail["shadow_rules"]] == [8]
    assert {r["id"] for r in detail["main_rules"]}.isdisjoint(
        r["id"] for r in detail["shadow_rules"]
    )
    assert detail["report"]["rule_pack"]["shadow_enabled"] is True

    # 处置只认主集：SH-2/SH-3 隔离，SH-1/SH-4 签发（4 行扫描）
    assert detail["scanned_count"] == 4
    assert detail["quarantined_count"] == 2
    assert detail["passed_count"] == 2
    assert detail["released_count"] == 2

    rows = {r["entity_key"]: r for r in detail["rows"]}
    # 对照 1：主过影子中 —— SH-1 影子 tag 命中，但仍按主集 pass 并进精选区
    sh1 = rows["SH-1"]
    assert sh1["disposition"] == "pass"
    assert sh1["rule_hits"] == []
    assert len(sh1["shadow_hits"]) == 1 and sh1["shadow_hits"][0]["rule_id"] == 8
    # 对照 2：主中影子过 —— SH-2 主集 price error 隔离，影子未中
    sh2 = rows["SH-2"]
    assert sh2["disposition"] == "quarantine"
    assert [h["rule_id"] for h in sh2["rule_hits"]] == [7]
    assert sh2["shadow_hits"] == []
    # 两侧都中
    sh3 = rows["SH-3"]
    assert sh3["disposition"] == "quarantine"
    assert sh3["rule_hits"] and sh3["shadow_hits"]
    # 两侧都过
    assert rows["SH-4"]["disposition"] == "pass"
    assert rows["SH-4"]["rule_hits"] == [] and rows["SH-4"]["shadow_hits"] == []

    # 三类差分计数
    counts = detail["report"]["diff"]["counts"]
    assert counts == {"shadow_only": 1, "main_only": 1, "both_hit": 1}
    diff_rows = {
        bucket: [d["entity_key"] for d in items]
        for bucket, items in detail["report"]["diff"]["details"].items()
    }
    assert diff_rows["shadow_only"] == ["SH-1"]
    assert diff_rows["main_only"] == ["SH-2"]
    assert diff_rows["both_hit"] == ["SH-3"]


def test_shadow_hits_persisted_but_never_quarantine_or_block_release(env):
    from app.db import connect

    detail = env.run_inspect(SHADOW_DATASET)
    bid = detail["id"]
    conn = connect()
    try:
        # 行级影子命中独立持久化
        shadow_rows = conn.execute(
            "SELECT row_no, entity_key, rule_id FROM shadow_hits WHERE batch_id=? ORDER BY row_no",
            (bid,),
        ).fetchall()
        assert [dict(r) for r in shadow_rows] == [
            {"row_no": 1, "entity_key": "SH-1", "rule_id": 8},
            {"row_no": 3, "entity_key": "SH-3", "rule_id": 8},
        ]
        # 隔离区只有主集 error 行（SH-2/SH-3），影子多拦的 SH-1 不在其中
        q = conn.execute(
            "SELECT entity_key FROM quarantine WHERE batch_id=? ORDER BY row_no", (bid,)
        ).fetchall()
        assert [r["entity_key"] for r in q] == ["SH-2", "SH-3"]
        # 精选区按主集签发：SH-1 虽被影子命中仍在，隔离行不在
        cu = conn.execute(
            "SELECT entity_key FROM curated WHERE batch_id=? ORDER BY entity_key", (bid,)
        ).fetchall()
        assert [r["entity_key"] for r in cu] == ["SH-1", "SH-4"]
    finally:
        conn.close()

    # 全部对账项通过（数字/行清单/隔离区/精选区/影子命中互证）
    for name in (
        "scan_breakdown",
        "release_matches_passed",
        "quarantine_table_matches",
        "curated_table_matches",
        "shadow_hits_persisted",
        "diff_counts_match_rows",
        "shadow_never_blocks",
    ):
        _check(detail, name)


def test_shadow_disabled_equals_single_main_track(env):
    """Orders/Inventory 没有影子规则 → 等价单主集入检。"""
    detail = env.run_inspect(1)
    assert detail["shadow_rules"] == []
    assert detail["report"]["rule_pack"]["shadow_enabled"] is False
    assert detail["shadow_hit_total"] == 0
    assert detail["report"]["diff"]["counts"]["shadow_only"] == 0
    _check(detail, "shadow_pack_disabled_single_track")
    # 主集 error 仍按原口径隔离
    assert detail["quarantined_count"] == 2


def test_rule_changes_do_not_rewrite_finished_batch(env):
    from app.db import connect
    from app.repositories import batches as batch_repo
    from app.repositories import rules as rules_repo
    from app.repositories import shadow_hits as shadow_repo

    detail = env.run_inspect(SHADOW_DATASET)
    bid = detail["id"]
    main_ids = [r["id"] for r in detail["main_rules"]]
    shadow_ids = [r["id"] for r in detail["shadow_rules"]]
    diff_before = dict(detail["report"]["diff"]["counts"])

    conn = connect()
    try:
        shadow_n_before = shadow_repo.count_by_batch(conn, bid)
        # 改动归属与启停：影子规则转主集、一条主集规则停用
        rules_repo.set_track(conn, 8, "main")
        rules_repo.set_enabled(conn, 6, False)
        conn.commit()
    finally:
        conn.close()

    # 已结束批次的两套清单、差分、行级影子命中、隔离/精选均不回变
    conn = connect()
    try:
        frozen = batch_repo.get_batch(conn, bid)
        assert [r["id"] for r in frozen["main_rules"]] == main_ids
        assert [r["id"] for r in frozen["shadow_rules"]] == shadow_ids
        assert frozen["report"]["diff"]["counts"] == diff_before
        assert shadow_repo.count_by_batch(conn, bid) == shadow_n_before
        assert conn.execute(
            "SELECT COUNT(*) c FROM quarantine WHERE batch_id=?", (bid,)
        ).fetchone()["c"] == 2
        assert conn.execute(
            "SELECT COUNT(*) c FROM curated WHERE batch_id=?", (bid,)
        ).fetchone()["c"] == 2
    finally:
        conn.close()

    # 新批次按新归属评估，且一条规则不会同时属两边
    new_detail = env.run_inspect(SHADOW_DATASET)
    assert [r["id"] for r in new_detail["main_rules"]] == [7, 8]
    assert new_detail["shadow_rules"] == []


def test_track_must_be_main_or_shadow_and_exclusive(env):
    from app.modules.shadow_rule_pack import MAIN, SHADOW, TrackError, normalize_track, split_rules

    assert normalize_track("main") == MAIN
    assert normalize_track("shadow") == SHADOW
    for bad in ("both", "", None, "MAIN"):
        with pytest.raises(TrackError):
            normalize_track(bad)

    # 拆分后同一条规则只出现在一边
    rules = [
        {"id": 1, "enabled": 1, "track": "main"},
        {"id": 2, "enabled": 1, "track": "shadow"},
        {"id": 3, "enabled": 0, "track": "shadow"},  # 停用不参与
    ]
    main_rules, shadow_rules = split_rules(rules)
    assert [r["id"] for r in main_rules] == [1]
    assert [r["id"] for r in shadow_rules] == [2]


def test_persistence_survives_reload(env):
    """刷新/重开连接后数字与行清单仍在（本地 SQLite 持久化）。"""
    detail = env.run_inspect(SHADOW_DATASET)
    bid = detail["id"]

    from app.db import connect
    from app.repositories import batches as batch_repo
    from app.repositories import shadow_hits as shadow_repo

    conn = connect()
    try:
        reloaded = batch_repo.get_batch(conn, bid)
        assert reloaded["report"]["diff"]["counts"] == {
            "shadow_only": 1,
            "main_only": 1,
            "both_hit": 1,
        }
        rows = batch_repo.list_batch_rows(conn, bid)
        assert len(rows) == 4
        assert sum(len(r["shadow_hits"]) for r in rows) == 2
        assert shadow_repo.count_by_batch(conn, bid) == 2
    finally:
        conn.close()
