import httpx
import pytest

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
