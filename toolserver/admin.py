from fastapi import APIRouter

from .state import world

router = APIRouter(prefix="/_admin")


@router.post("/reset")
def reset(seed: int = 0):
    world.reset(seed)
    return {"ok": True, "seed": seed}


@router.get("/state")
def state():
    return world.snapshot()