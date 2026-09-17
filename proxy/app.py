import os
import random

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import config

UPSTREAM = os.getenv("UPSTREAM_URL", "http://127.0.0.1:8100")

app = FastAPI(title="faultline proxy")

state = {
    "config": config.load("none"),
    "rng": random.Random(0),
    "injections": [],
}

client = httpx.Client(base_url=UPSTREAM, timeout=60.0)


@app.post("/_proxy/config")
def set_config(name: str, seed: int = 0):
    cfg = config.load(name)
    state["config"] = cfg
    state["rng"] = random.Random(seed)
    state["injections"] = []
    return {"ok": True, "config": name, "faults": [f.id for f in cfg.faults], "seed": seed}


@app.get("/_proxy/injections")
def injections():
    return {"count": len(state["injections"]), "injections": state["injections"]}


@app.api_route("/{path:path}", methods=["GET", "POST"])
def forward(path: str, request: Request):
    tool_name = path.split("/")[-1]
    is_tool = path.startswith("tools/")

    try:
        upstream = client.request(
            request.method,
            "/" + path,
            params=dict(request.query_params),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": "upstream_unreachable", "detail": type(exc).__name__},
        )

    if not is_tool:
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    rng = state["rng"]
    for fault in state["config"].faults:
        if not fault.matches(tool_name):
            continue
        if not fault.should_fire(rng):
            continue
        result = fault.apply(tool_name, upstream, rng)
        if not result.fired:
            continue
        state["injections"].append({"tool": tool_name, "fault": result.fault_id})
        headers = dict(result.headers or {})
        headers["x-faultline-injected"] = result.fault_id
        return Response(
            content=result.body,
            status_code=result.status_code,
            media_type="application/json",
            headers=headers,
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )