import os
import random

import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import config

app = FastAPI(title="faultline proxy")

state = {
    "config": config.load("none"),
    "rng": random.Random(0),
    "injections": [],
    "client": None,
}


def set_upstream(url: str):
    old = state["client"]
    state["client"] = httpx.Client(base_url=url, timeout=60.0)
    if old is not None:
        old.close()


set_upstream(os.getenv("UPSTREAM_URL", "http://127.0.0.1:8100"))


@app.post("/_proxy/config")
def set_config(name: str, seed: int = 0, inline: dict | None = Body(None)):
    """Activate a fault config by name, or pass the config itself as the JSON body."""
    try:
        cfg = config.parse(name, inline) if inline is not None else config.load(name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"bad fault config: {exc}")
    state["config"] = cfg
    state["rng"] = random.Random(seed)
    state["injections"] = []
    return {"ok": True, "config": name, "faults": [f.id for f in cfg.faults], "seed": seed}


@app.get("/_proxy/status")
def status():
    return {
        "upstream": str(state["client"].base_url),
        "config": state["config"].name,
        "injections": len(state["injections"]),
    }


@app.get("/_proxy/injections")
def injections():
    return {"count": len(state["injections"]), "injections": state["injections"]}


def _passthrough(upstream: httpx.Response) -> Response:
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


def _inject(fault, tool_name: str, upstream: httpx.Response | None) -> Response | None:
    result = fault.apply(tool_name, upstream, state["rng"])
    if not result.fired:
        return None
    fault.fires += 1
    state["injections"].append({"tool": tool_name, "fault": result.fault_id, "phase": fault.phase})
    headers = dict(result.headers or {})
    headers["x-faultline-injected"] = result.fault_id
    return Response(
        content=result.body,
        status_code=result.status_code,
        media_type="application/json",
        headers=headers,
    )


@app.api_route("/{path:path}", methods=["GET", "POST"])
def forward(path: str, request: Request):
    tool_name = path.split("/")[-1]
    is_tool = path.startswith("tools/")
    rng = state["rng"]
    active = [f for f in state["config"].faults if f.matches(tool_name)] if is_tool else []

    for fault in active:
        if fault.phase == "request" and fault.should_fire(rng):
            injected = _inject(fault, tool_name, None)
            if injected is not None:
                return injected

    try:
        upstream = state["client"].request(
            request.method,
            "/" + path,
            params=dict(request.query_params),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": "upstream_unreachable", "detail": type(exc).__name__},
        )

    for fault in active:
        if fault.phase == "response" and fault.should_fire(rng):
            injected = _inject(fault, tool_name, upstream)
            if injected is not None:
                return injected

    return _passthrough(upstream)
