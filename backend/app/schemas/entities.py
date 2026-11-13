from pydantic import BaseModel

from app.modules.shadow_rule_pack import TRACKS


class InspectRequest(BaseModel):
    dataset_id: int


class RuleEnablePatch(BaseModel):
    enabled: bool


class RuleTrackPatch(BaseModel):
    track: str

    def normalized(self) -> str:
        if self.track not in TRACKS:
            raise ValueError(f"track 必须是 {TRACKS[0]!r} 或 {TRACKS[1]!r}")
        return self.track
