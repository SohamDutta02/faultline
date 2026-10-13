import asyncio
import json
import random
import re
from collections.abc import Callable
from contextlib import asynccontextmanager, closing
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

import settings
from agents.llm import Backend, default_backend
from agents.registry import ARCHITECTURES
from proxy import config as fault_config
from proxy.faults import REGISTRY
from runner.stack import local_stack
from runner.trial import run_trial
from sweep import report, store
from sweep.run import FAULT_CONFIGS, LLM_ARCHITECTURES, execute, plan, set_fault_config
from tasks.catalog import BY_ID, CATALOG

from .auth import BasicAuth
from .jobs import Busy, Job, JobManager

PLAYGROUND = "playground"
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


class TrialRequest(BaseModel):
    task_id: str
    architecture_id: str
    fault_config: str = "none"
    seed: int | None = None
    max_steps: int = Field(12, ge=1, le=50)


class SweepRequest(BaseModel):
    label: str
    archs: list[str] = Field(min_length=1)
    configs: list[str] = Field(min_length=1)
    tasks: list[str] = Field(min_length=1)
    repeats: int = Field(3, ge=1, le=20)
    pace: float = Field(15.0, ge=0, le=300)
    max_steps: int = Field(12, ge=1, le=50)


def create_app(
    db_path: str | Path | None = None,
    toolserver_url: str | None = None,
    proxy_url: str | None = None,
    serve_stack: bool = True,
    backend_factory: Callable[[], Backend] | None = None,
    llm_available: bool | None = None,
    static_dir: Path | None = WEB_DIST,
    login: tuple[str, str] | None = None,
) -> FastAPI:
    """The faultline web app. By default it runs its own toolserver and proxy
    in-process; pass serve_stack=False with URLs to use running ones.

    login is (username, password); by default it comes from FAULTLINE_USER and
    FAULTLINE_PASSWORD, and an empty password leaves the app open."""
    jobs = JobManager()
    urls = {"toolserver": toolserver_url or settings.TOOLSERVER_URL, "proxy": proxy_url or settings.PROXY_URL}
    make_backend = backend_factory or default_backend

    def has_llm() -> bool:
        return llm_available if llm_available is not None else bool(settings.gemini_key())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if serve_stack:
            with local_stack() as (ts, px):
                urls.update(toolserver=ts, proxy=px)
                yield
        else:
            yield

    app = FastAPI(title="faultline", lifespan=lifespan)
    app.state.jobs = jobs
    username, password = login if login is not None else settings.app_login()
    if password:
        app.add_middleware(BasicAuth, username=username, password=password)

    def db():
        return closing(store.connect(db_path, check_same_thread=False))

    # ---- catalog -------------------------------------------------------------

    @app.get("/api/meta")
    def meta():
        configs = []
        for name in fault_config.available():
            cfg = fault_config.load(name)
            configs.append({
                "name": name,
                "faults": [
                    {
                        "type": f.id, "layer": f.layer, "phase": f.phase, "rate": f.rate,
                        "targets": f.targets, "max_fires": f.max_fires, "params": f.params,
                    }
                    for f in cfg.faults
                ],
            })
        configs.sort(key=lambda c: (FAULT_CONFIGS.index(c["name"]) if c["name"] in FAULT_CONFIGS else 99, c["name"]))
        return {
            "model": settings.MODEL,
            "llm_available": has_llm(),
            "architectures": [
                {"id": a.id, "description": a.description, "needs_llm": a.needs_llm,
                 "retries": a.toolbox.__name__ == "ResilientToolbox"}
                for a in ARCHITECTURES.values()
            ],
            "tasks": [
                {"id": t.id, "prompt": t.prompt, "category": t.category, "seed": t.seed,
                 "checks": [c.check_name for c in t.checks], "setup": t.setup}
                for t in CATALOG
            ],
            "fault_configs": configs,
            "fault_types": {
                k: {"layer": c.layer, "phase": c.phase, "description": c.description} for k, c in REGISTRY.items()
            },
            "sweep_defaults": {"configs": FAULT_CONFIGS, "archs": LLM_ARCHITECTURES, "tasks": [t.id for t in CATALOG]},
        }

    # ---- jobs ----------------------------------------------------------------

    def busy_response(exc: Busy):
        return JSONResponse(status_code=409, content={"detail": str(exc), "job": exc.job.summary()})

    @app.get("/api/jobs/current")
    def current_job():
        job = jobs.running() or jobs.current
        return job.summary() if job else None

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job.summary()

    @app.post("/api/jobs/{job_id}/stop")
    def stop_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        job.stop_requested.set()
        return job.summary()

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        last = request.headers.get("last-event-id")
        start = int(last) + 1 if last and last.isdigit() else 0

        async def stream():
            i, ticks = start, 0
            while True:
                while i < len(job.events):
                    ev = job.events[i]
                    i += 1
                    yield f"id: {ev['seq']}\ndata: {json.dumps(ev, default=str)}\n\n"
                    if ev["type"] == "end":
                        return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.1)
                ticks += 1
                if ticks % 150 == 0:
                    yield ": keepalive\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    # ---- playground trials ---------------------------------------------------

    def check_archs(arch_ids: list[str]):
        for a in arch_ids:
            if a not in ARCHITECTURES:
                raise HTTPException(400, f"unknown architecture {a}")
            if ARCHITECTURES[a].needs_llm and not has_llm():
                raise HTTPException(400, f"{a} needs a model; set GEMINI_API_KEY in .env and restart")

    def check_configs(names: list[str]):
        unknown = sorted(set(names) - set(fault_config.available()))
        if unknown:
            raise HTTPException(400, f"unknown fault configs {unknown}")

    @app.post("/api/trials", status_code=202)
    def start_trial(req: TrialRequest):
        if req.task_id not in BY_ID:
            raise HTTPException(400, f"unknown task {req.task_id}")
        check_archs([req.architecture_id])
        check_configs([req.fault_config])
        params = req.model_dump()
        params["seed"] = req.seed if req.seed is not None else random.randrange(1_000_000)

        def target(job: Job):
            p = job.params
            arch = ARCHITECTURES[p["architecture_id"]]
            backend = make_backend() if arch.needs_llm else None
            set_fault_config(urls["proxy"], p["fault_config"], p["seed"])
            job.emit("start", **p)
            result = run_trial(
                BY_ID[p["task_id"]], p["architecture_id"], backend, p["max_steps"],
                urls["toolserver"], urls["proxy"], on_event=lambda e: job.emit("event", event=asdict(e)),
            )
            with db() as conn:
                sid = store.start_sweep(conn, PLAYGROUND, "mixed", 1)
                rep = store.next_repeat(conn, sid, result.task_id, result.architecture_id, p["fault_config"])
                trial_id = store.record(conn, sid, p["fault_config"], rep, p["seed"], result)
                store.finish_sweep(conn, sid, "done")
            summary = {k: v for k, v in asdict(result).items() if k != "events"}
            job.result = {**summary, "trial_id": trial_id, "fault_config": p["fault_config"], "seed": p["seed"]}
            job.emit("result", result=job.result)

        title = f"{req.task_id} / {req.architecture_id} / {req.fault_config}"
        try:
            job = jobs.start("trial", title, params, target)
        except Busy as exc:
            return busy_response(exc)
        return job.summary()

    @app.get("/api/trials/{trial_id}")
    def get_trial(trial_id: str):
        with db() as conn:
            trial = store.get_trial(conn, trial_id)
        if trial is None:
            raise HTTPException(404, "no such trial")
        return trial

    @app.get("/api/playground/runs")
    def playground_runs(limit: int = Query(25, ge=1, le=200)):
        with db() as conn:
            sweep = store.find_sweep(conn, PLAYGROUND)
            return store.list_trials(conn, sweep["id"], limit=limit) if sweep else []

    # ---- sweeps --------------------------------------------------------------

    @app.get("/api/sweeps")
    def list_sweeps():
        live = jobs.running()
        live_label = live.params.get("label") if live and live.kind == "sweep" else None
        with db() as conn:
            rows = [s for s in store.list_sweeps(conn) if s["label"] != PLAYGROUND]
        for s in rows:
            p = s["params"]
            s["planned"] = len(p["archs"]) * len(p["configs"]) * len(p["tasks"]) * p["repeats"] if p else None
            s["live"] = s["label"] == live_label
        return rows

    @app.post("/api/sweeps", status_code=202)
    def start_sweep(req: SweepRequest):
        if not LABEL.match(req.label) or req.label == PLAYGROUND:
            raise HTTPException(400, "label must be 1-64 letters, digits, '.', '_' or '-', and not 'playground'")
        check_archs(req.archs)
        check_configs(req.configs)
        unknown = sorted(set(req.tasks) - set(BY_ID))
        if unknown:
            raise HTTPException(400, f"unknown tasks {unknown}")

        def target(job: Job):
            p = job.params
            needs_llm = any(ARCHITECTURES[a].needs_llm for a in p["archs"])
            backend = make_backend() if needs_llm else None
            with db() as conn:
                matrix = {k: p[k] for k in ("archs", "configs", "tasks", "repeats")}
                sweep_id = store.start_sweep(conn, p["label"], backend.model if backend else "none", p["repeats"], matrix)
                total, todo = plan(p["repeats"], p["configs"], p["tasks"], p["archs"], store.done_cells(conn, sweep_id))
                job.progress = {"done": 0, "total": len(todo), "planned": total}
                job.emit("plan", label=p["label"], total=total, todo=len(todo))

                def progress(i, n, cell, result, trial_id, note):
                    job.progress = {"done": i, "total": n, "planned": total}
                    job.emit(
                        "progress", i=i, n=n, cell=asdict(cell), outcome=result.outcome,
                        fault_fired=result.fault_fired, trial_id=trial_id, note=note,
                    )

                status = execute(
                    conn, sweep_id, todo, backend, urls["toolserver"], urls["proxy"], p["max_steps"], p["pace"],
                    on_progress=progress, should_stop=job.stop_requested.is_set,
                )
                store.finish_sweep(conn, sweep_id, status)
            job.status = status

        try:
            job = jobs.start("sweep", req.label, req.model_dump(), target)
        except Busy as exc:
            return busy_response(exc)
        return job.summary()

    @app.get("/api/sweeps/{label}")
    def get_sweep(label: str):
        with db() as conn:
            summary = report.summarize(conn, label)
            row = store.find_sweep(conn, label)
        if summary is None:
            raise HTTPException(404, "no such sweep")
        summary["params"] = json.loads(row["params"]) if row["params"] else None
        live = jobs.running()
        summary["live_job"] = live.summary() if live and live.kind == "sweep" and live.params["label"] == label else None
        return summary

    @app.get("/api/sweeps/{label}/trials")
    def sweep_trials(
        label: str,
        config: str | None = None,
        arch: str | None = None,
        task: str | None = None,
        outcome: str | None = None,
        limit: int = Query(500, ge=1, le=5000),
    ):
        with db() as conn:
            sweep = store.find_sweep(conn, label)
            if sweep is None:
                raise HTTPException(404, "no such sweep")
            filters = {"fault_config": config, "architecture_id": arch, "task_id": task, "outcome": outcome}
            return store.list_trials(conn, sweep["id"], filters, limit)

    @app.get("/api/health")
    def health():
        return {"ok": True, "toolserver": urls["toolserver"], "proxy": urls["proxy"]}

    # ---- web UI --------------------------------------------------------------

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        if static_dir is None or not (static_dir / "index.html").exists():
            return PlainTextResponse("web UI not built: run `npm ci && npm run build` in web/", status_code=503)
        file = (static_dir / path).resolve()
        if path and file.is_file() and file.is_relative_to(static_dir.resolve()):
            return FileResponse(file)
        return FileResponse(static_dir / "index.html")

    return app
