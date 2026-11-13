from fastapi import APIRouter, HTTPException

from app.db import connect
from app.modules.shadow_rule_pack import TrackError
from app.repositories import rules as rules_repo
from app.schemas.entities import RuleEnablePatch, RuleTrackPatch

router = APIRouter(tags=["rules"])


@router.get("/rules")
def list_rules(dataset_id: int | None = None):
    conn = connect()
    try:
        return rules_repo.list_rules(conn, dataset_id)
    finally:
        conn.close()


@router.patch("/rules/{rule_id}")
def patch_rule(rule_id: int, body: RuleEnablePatch):
    conn = connect()
    try:
        existing = rules_repo.get_rule(conn, rule_id)
        if not existing:
            raise HTTPException(404, "rule not found")
        rules_repo.set_enabled(conn, rule_id, body.enabled)
        conn.commit()
        return {"id": rule_id, "enabled": body.enabled}
    finally:
        conn.close()


@router.patch("/rules/{rule_id}/track")
def patch_rule_track(rule_id: int, body: RuleTrackPatch):
    try:
        track = body.normalized()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = connect()
    try:
        existing = rules_repo.get_rule(conn, rule_id)
        if not existing:
            raise HTTPException(404, "rule not found")
        try:
            rules_repo.set_track(conn, rule_id, track)
        except TrackError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        return {"id": rule_id, "track": track}
    finally:
        conn.close()
