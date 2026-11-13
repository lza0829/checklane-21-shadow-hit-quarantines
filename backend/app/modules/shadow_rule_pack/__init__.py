"""影子规则包（shadow rule pack）：主/影子双轨规则评估。

- 每条规则归属且仅归属一条轨道：``main``（主集，参与处置）或 ``shadow``（影子集，只观察）。
- 影子命中只做记录与差分，永远不写入隔离、不改 disposition、不减少签发。
- 批次落库时固化两套规则清单快照，之后规则再怎么改动都不会回写已结束批次。
"""

from __future__ import annotations

from typing import Any

MAIN = "main"
SHADOW = "shadow"
TRACKS = (MAIN, SHADOW)

# 三类差分桶 + 一个“两侧都过”的补集桶
BOTH_HIT = "both_hit"          # 两侧都中
MAIN_ONLY = "main_only"        # 主中影子过
SHADOW_ONLY = "shadow_only"    # 主过影子中
NEITHER = "neither"            # 两侧都过（不计入三类差分）
DIFF_BUCKETS = (SHADOW_ONLY, MAIN_ONLY, BOTH_HIT)

_BUCKET_LABELS = {
    SHADOW_ONLY: "主过影子中",
    MAIN_ONLY: "主中影子过",
    BOTH_HIT: "两侧都中",
    NEITHER: "两侧都过",
}

# 快照只固化评估时真正用到的字段；id 是回溯锚点，config 字段保证可复盘
_SNAPSHOT_FIELDS = ("id", "name", "field", "rule_type", "severity", "enabled", "track")


class TrackError(ValueError):
    """规则轨道归属非法（不是 main/shadow，或一条规则想同时属两边）。"""


def normalize_track(track: Any) -> str:
    if track not in TRACKS:
        raise TrackError(f"track 必须是 {TRACKS[0]!r} 或 {TRACKS[1]!r}，实际 {track!r}")
    return str(track)


def split_rules(rules: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按轨道拆分**已启用**规则。同一规则只可能出现在其中一边。"""
    main_rules: list[dict[str, Any]] = []
    shadow_rules: list[dict[str, Any]] = []
    for rule in rules:
        if not rule.get("enabled", 1):
            continue
        track = normalize_track(rule.get("track", MAIN))
        if track == MAIN:
            main_rules.append(rule)
        else:
            shadow_rules.append(rule)
    return main_rules, shadow_rules


def rule_snapshot(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批次开始时固化两套清单；后续规则改动不影响该快照。"""
    return [{k: rule.get(k) for k in _SNAPSHOT_FIELDS} for rule in rules]


def classify_row(main_hits: list[dict[str, Any]], shadow_hits: list[dict[str, Any]]) -> str:
    """按命中集合把一行归入四类之一（warn 命中也算“中”）。"""
    main_hit = bool(main_hits)
    shadow_hit = bool(shadow_hits)
    if main_hit and shadow_hit:
        return BOTH_HIT
    if main_hit:
        return MAIN_ONLY
    if shadow_hit:
        return SHADOW_ONLY
    return NEITHER


def bucket_label(bucket: str) -> str:
    return _BUCKET_LABELS.get(bucket, bucket)


def empty_diff_counts() -> dict[str, int]:
    return {bucket: 0 for bucket in DIFF_BUCKETS}


def diff_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总三类差分计数与行级明细；明细按桶分组、桶内按行号排序。"""
    counts = empty_diff_counts()
    details: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in DIFF_BUCKETS}
    for row in rows:
        bucket = classify_row(row.get("main_hits", []), row.get("shadow_hits", []))
        if bucket == NEITHER:
            continue
        counts[bucket] += 1
        details[bucket].append(
            {
                "row_no": row["row_no"],
                "entity_key": row.get("entity_key"),
                "bucket": bucket,
                "label": bucket_label(bucket),
                "main_hits": row.get("main_hits", []),
                "shadow_hits": row.get("shadow_hits", []),
            }
        )
    total = sum(counts.values())
    return {
        "counts": counts,
        "labels": {bucket: bucket_label(bucket) for bucket in DIFF_BUCKETS},
        "total_diff_rows": total,
        "details": details,
    }


def shadow_hit_records(
    batch_id: int,
    row_no: int,
    entity_key: str | None,
    shadow_hits: list[dict[str, Any]],
    created_at: str,
) -> list[tuple[Any, ...]]:
    """把行级影子命中展开成 shadow_hits 表的插入参数（只记录，不处置）。"""
    return [
        (
            batch_id,
            row_no,
            entity_key,
            hit["rule_id"],
            hit["name"],
            hit["field"],
            hit["rule_type"],
            hit["severity"],
            hit.get("detail", ""),
            created_at,
        )
        for hit in shadow_hits
    ]
