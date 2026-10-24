import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app

from runner.stack import local_stack


@pytest.fixture(scope="session")
def stack():
    with local_stack() as urls:
        yield urls


@pytest.fixture
def urls(stack):
    """A running stack with no faults active and the world reset to seed 42."""
    toolserver_url, proxy_url = stack
    httpx.post(f"{proxy_url}/_proxy/config", params={"name": "none"}).raise_for_status()
    httpx.post(f"{toolserver_url}/_admin/reset", params={"seed": 42}).raise_for_status()
    return stack


@pytest.fixture
def faults(urls):
    """Activate an inline fault config on the proxy: faults([{...}], seed=0)."""
    _, proxy_url = urls

    def activate(entries: list[dict], seed: int = 0):
        r = httpx.post(
            f"{proxy_url}/_proxy/config",
            params={"name": "test", "seed": seed},
            json={"faults": entries},
        )
        r.raise_for_status()
        return r.json()

    return activate


@pytest.fixture
def make_client(stack, tmp_path):
    toolserver_url, proxy_url = stack
    clients = []

    def make(**kw):
        kw.setdefault("llm_available", False)
        kw.setdefault("static_dir", None)
        kw.setdefault("login", ("admin", ""))  # hermetic: ignore a FAULTLINE_PASSWORD in the environment
        app = create_app(db_path=tmp_path / "api.db", toolserver_url=toolserver_url, proxy_url=proxy_url,
                         serve_stack=False, **kw)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for c in clients:
        c.__exit__(None, None, None)
