from __future__ import annotations

from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.parse_fixture import parse_jsonl
from app.engines.rule_engine import decide_disposition, evaluate_row
from app.modules.shadow_rule_pack import (
    SHADOW_ONLY,
    diff_summary,
    rule_snapshot,
    shadow_hit_records,
    split_rules,
)
from app.repositories import batches as batch_repo
from app.repositories import curated as curated_repo
from app.repositories import datasets as dataset_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import rules as rules_repo
from app.repositories import shadow_hits as shadow_repo
from app.seed import _now


def run_inspect(dataset_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        ds = dataset_repo.get_dataset(conn, dataset_id)
        if not ds:
            raise ValueError(f"dataset {dataset_id} not found")
        if not ds["enabled"]:
            raise ValueError(f"dataset {dataset_id} disabled")

        path = FIXTURES_DIR / ds["fixture_path"]
        rows = parse_jsonl(path)

        # 双轨拆分：同一条规则只可能落在一边；快照在评估前固化，
        # 之后任何规则改动都不会改写本批的两套清单。
        enabled_rules = [r for r in rules_repo.list_rules(conn, dataset_id) if r.get("enabled")]
        main_rules, shadow_rules = split_rules(enabled_rules)
        main_snapshot = rule_snapshot(main_rules)
        shadow_snapshot = rule_snapshot(shadow_rules)

        scanned = len(rows)
        passed = failed = quarantined = released = 0
        row_results: list[dict[str, Any]] = []
        now = _now()

        # 先插批次空壳拿 id；计数算完后回写报告。
        # 每行两套规则都必须评估完毕，才允许出报告。
        for idx, payload in enumerate(rows, start=1):
            # 主集评估
            main_hits = evaluate_row(payload, main_rules)
            # 影子集评估 → 只观察：命中只进 shadow_hits / 差分账，
            # 不参与处置裁决，不进隔离、不改 disposition、不减少签发。
            shadow_hits = evaluate_row(payload, shadow_rules)
            disposition = decide_disposition(main_hits)

            entity_key = str(payload.get("entity_key") or "") or None
            if disposition == "quarantine":
                quarantined += 1
                failed += 1
            else:
                passed += 1

            row_results.append(
                {
                    "row_no": idx,
                    "entity_key": entity_key,
                    "payload": payload,
                    "disposition": disposition,
                    "main_hits": main_hits,
                    "shadow_hits": shadow_hits,
                }
            )

        diff = diff_summary(
            [
                {
                    "row_no": item["row_no"],
                    "entity_key": item["entity_key"],
                    "main_hits": item["main_hits"],
                    "shadow_hits": item["shadow_hits"],
                }
                for item in row_results
            ]
        )

        checks = [
            {
                "name": "scan_breakdown",
                "ok": scanned == passed + quarantined,
                "detail": f"scanned({scanned}) = passed({passed}) + quarantined({quarantined})",
            }
        ]

        report: dict[str, Any] = {
            "dataset_id": dataset_id,
            "dataset_name": ds["name"],
            "fixture_path": ds["fixture_path"],
            "rule_count": len(enabled_rules),
            "rule_pack": {
                "main": main_snapshot,
                "shadow": shadow_snapshot,
                "main_rule_count": len(main_snapshot),
                "shadow_rule_count": len(shadow_snapshot),
                "shadow_enabled": bool(shadow_snapshot),
            },
            "counts": {
                "scanned": scanned,
                "passed": passed,
                "failed": failed,
                "quarantined": quarantined,
                "released": 0,
            },
            "diff": diff,
            "checks": checks,
        }
        batch_id = batch_repo.insert_batch(
            conn,
            dataset_id=dataset_id,
            status="inspected",
            scanned=scanned,
            passed=passed,
            failed=failed,
            quarantined=quarantined,
            released=0,
            report=report,
            main_rules=main_snapshot,
            shadow_rules=shadow_snapshot,
            created_at=now,
        )

        # 行级影子命中持久化（独立于隔离/精选，只观察）
        shadow_records: list[tuple[Any, ...]] = []
        for item in row_results:
            shadow_records.extend(
                shadow_hit_records(batch_id, item["row_no"], item["entity_key"], item["shadow_hits"], now)
            )
        shadow_repo.insert_many(conn, shadow_records)

        for item in row_results:
            batch_repo.insert_row(
                conn,
                batch_id=batch_id,
                row_no=item["row_no"],
                entity_key=item["entity_key"],
                payload=item["payload"],
                disposition=item["disposition"],
                rule_hits=item["main_hits"],
                shadow_hits=item["shadow_hits"],
            )
            if item["disposition"] == "quarantine":
                # 隔离原因只取主集 error 命中；影子命中永远不进隔离
                reasons = "; ".join(
                    f"{h['name']}:{h['detail']}"
                    for h in item["main_hits"]
                    if h.get("severity") == "error"
                )
                quarantine_repo.insert(
                    conn,
                    batch_id=batch_id,
                    dataset_id=dataset_id,
                    row_no=item["row_no"],
                    entity_key=item["entity_key"],
                    reason=reasons or "rule hit",
                    payload=item["payload"],
                    created_at=now,
                )
            else:
                # 放行/签发只认主集：哪怕影子命中（主过影子中）也照常进精选区
                key = item["entity_key"] or f"row-{item['row_no']}"
                curated_repo.upsert(
                    conn,
                    dataset_id=dataset_id,
                    batch_id=batch_id,
                    entity_key=key,
                    payload=item["payload"],
                    released_at=now,
                )
                released += 1

        # ---- 对账：数字、行清单、隔离区、精选区、影子命中五处互证 ----
        report["counts"]["released"] = released
        report["checks"].append(
            {
                "name": "release_matches_passed",
                "ok": released == passed,
                "detail": f"released({released}) = passed({passed})，影子命中不减少签发",
            }
        )
        batch_quarantine = conn.execute(
            "SELECT COUNT(*) AS c FROM quarantine WHERE batch_id=?", (batch_id,)
        ).fetchone()["c"]
        report["checks"].append(
            {
                "name": "quarantine_table_matches",
                "ok": batch_quarantine == quarantined,
                "detail": f"本批隔离表 {batch_quarantine} 行 = 主集裁决隔离 {quarantined} 行（影子命中不入隔离）",
            }
        )
        batch_curated = conn.execute(
            "SELECT COUNT(*) AS c FROM curated WHERE batch_id=?", (batch_id,)
        ).fetchone()["c"]
        report["checks"].append(
            {
                "name": "curated_table_matches",
                "ok": batch_curated == released,
                "detail": f"本批精选表 {batch_curated} 行 = 主集放行签发 {released} 行",
            }
        )
        persisted_shadow = shadow_repo.count_by_batch(conn, batch_id)
        expected_shadow = sum(len(item["shadow_hits"]) for item in row_results)
        report["checks"].append(
            {
                "name": "shadow_hits_persisted",
                "ok": persisted_shadow == expected_shadow,
                "detail": f"shadow_hits 表 {persisted_shadow} 条 = 行清单影子命中 {expected_shadow} 条",
            }
        )
        # 三类差分计数必须能从行清单重新算出来
        recomputed = diff_summary(
            [
                {
                    "row_no": item["row_no"],
                    "entity_key": item["entity_key"],
                    "main_hits": item["main_hits"],
                    "shadow_hits": item["shadow_hits"],
                }
                for item in row_results
            ]
        )["counts"]
        report["checks"].append(
            {
                "name": "diff_counts_match_rows",
                "ok": recomputed == diff["counts"],
                "detail": f"差分计数 {diff['counts']} 与行清单重算结果一致",
            }
        )
        # 「主过影子中」差分计数必须与 shadow_hits 行级账可核对：
        # 差分账里的行集合 == shadow_hits 表中“主集无命中”的行集合。
        shadow_only_row_nos = {r["row_no"] for r in diff["details"][SHADOW_ONLY]}
        main_hit_row_nos = {
            item["row_no"] for item in row_results if item["main_hits"]
        }
        persisted_rows = shadow_repo.list_by_batch(conn, batch_id)
        ledger_shadow_only = {
            r["row_no"] for r in persisted_rows if r["row_no"] not in main_hit_row_nos
        }
        report["checks"].append(
            {
                "name": "shadow_only_diff_matches_ledger",
                "ok": ledger_shadow_only == shadow_only_row_nos,
                "detail": (
                    f"主过影子中差分 {len(shadow_only_row_nos)} 行 {sorted(shadow_only_row_nos)} "
                    f"= shadow_hits 行级账（主集无命中）{len(ledger_shadow_only)} 行 {sorted(ledger_shadow_only)}"
                ),
            }
        )
        # 影子关闭 → 等价单主集：影子侧无任何命中、无“主过影子中”差分
        if not shadow_snapshot:
            report["checks"].append(
                {
                    "name": "shadow_pack_disabled_single_track",
                    "ok": expected_shadow == 0 and diff["counts"][SHADOW_ONLY] == 0,
                    "detail": "影子集为空，等价单主集入检；无影子命中、无主过影子中差分",
                }
            )
        # 主过影子中的行必须全部按主集放行，不得被影子拖进隔离
        shadow_only_rows = diff["details"][SHADOW_ONLY]
        quarantined_row_nos = {
            item["row_no"] for item in row_results if item["disposition"] == "quarantine"
        }
        report["checks"].append(
            {
                "name": "shadow_never_blocks",
                "ok": all(r["row_no"] not in quarantined_row_nos for r in shadow_only_rows),
                "detail": (
                    f"主过影子中 {len(shadow_only_rows)} 行全部按主集放行签发，影子不改变 disposition"
                ),
            }
        )

        batch_repo.update_report(
            conn,
            batch_id=batch_id,
            released=released,
            report=report,
            status="released" if released else "inspected",
        )
        conn.commit()

        detail = batch_repo.get_batch(conn, batch_id)
        assert detail is not None
        detail["rows"] = batch_repo.list_batch_rows(conn, batch_id)
        detail["shadow_hits"] = shadow_repo.list_by_batch(conn, batch_id)
        detail["shadow_hit_total"] = persisted_shadow
        detail["curated_total"] = curated_repo.count_all(conn, dataset_id)
        detail["quarantine_total"] = quarantine_repo.count_all(conn)
        return detail
    finally:
        conn.close()


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        datasets = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
        rules = conn.execute("SELECT COUNT(*) AS c FROM rules WHERE enabled=1").fetchone()["c"]
        main_rules = conn.execute(
            "SELECT COUNT(*) AS c FROM rules WHERE enabled=1 AND track='main'"
        ).fetchone()["c"]
        shadow_rules = conn.execute(
            "SELECT COUNT(*) AS c FROM rules WHERE enabled=1 AND track='shadow'"
        ).fetchone()["c"]
        batches = conn.execute("SELECT COUNT(*) AS c FROM inspect_batches").fetchone()["c"]
        return {
            "dataset_count": datasets,
            "enabled_rule_count": rules,
            "main_rule_count": main_rules,
            "shadow_rule_count": shadow_rules,
            "batch_count": batches,
            "curated_count": curated_repo.count_all(conn),
            "quarantine_count": quarantine_repo.count_all(conn),
        }
    finally:
        conn.close()
