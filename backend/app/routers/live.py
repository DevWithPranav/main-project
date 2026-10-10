"""Live vehicle state in (service token) and the live WebSocket out."""

from fastapi import APIRouter, Body, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from ..auth import Principal, principal_from_token, require
from ..live import hub

router = APIRouter()


class VehicleState(BaseModel):
    session_id: str
    track_id: int
    cls: str | None = None
    x: float
    y: float
    speed_kmh: float | None = None
    heading_deg: float | None = None
    lane_id: str | None = None
    state: str = "ok"
    t_s: float

    def model_post_init(self, _):
        if self.state not in ("ok", "checking", "flagged"):
            raise ValueError("state must be ok, checking or flagged")


@router.post("/live/state")
async def live_state(states: list[VehicleState] = Body(...), _: Principal = Depends(require(service=True))):
    if not states:
        raise HTTPException(422, "empty list")
    n = await hub.publish_states([s.model_dump() for s in states])
    return {"received": n}


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, token: str | None = None, session_id: str | None = None):
    if principal_from_token(token) is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
        return
    await ws.accept()
    try:
        await hub.serve(ws, session_id)
    except WebSocketDisconnect:
        pass
