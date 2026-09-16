from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import admin, tools
from .state import NotFound, Rejected

app = FastAPI(title="faultline toolserver")

app.include_router(tools.router)
app.include_router(admin.router)


@app.exception_handler(NotFound)
def not_found(request: Request, exc: NotFound):
    return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})


@app.exception_handler(Rejected)
def rejected(request: Request, exc: Rejected):
    return JSONResponse(status_code=409, content={"error": "rejected", "detail": str(exc)})


@app.get("/health")
def health():
    return {"ok": True}