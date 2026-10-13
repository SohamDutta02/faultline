import json

import httpx
import pytest

from proxy.config import available, load


def _orders(toolserver_url, customer_id="CUST-100"):
    return httpx.get(f"{toolserver_url}/tools/list_orders", params={"customer_id": customer_id}).json()["orders"]


def _place(proxy_url, qty=5):
    return httpx.post(
        f"{proxy_url}/tools/place_order", params={"customer_id": "CUST-100", "sku": "A-22", "qty": qty}
    )


@pytest.mark.parametrize("name", available())
def test_shipped_configs_load(name):
    load(name)


@pytest.mark.parametrize(
    "fault",
    [
        {"type": "rate_limit", "params": {"retry_after": 0}},
        {"type": "timeout", "params": {"delay_s": 0.05}},
    ],
)
def test_transport_faults_never_reach_upstream(urls, faults, fault):
    # regression: the proxy used to forward first, so a "rate limited" write had
    # already been committed and an agent retry created a duplicate
    toolserver_url, proxy_url = urls
    faults([{**fault, "target": "place_order"}])
    r = _place(proxy_url)
    assert r.status_code in (429, 504)
    assert r.headers["x-faultline-injected"] == fault["type"]
    assert _orders(toolserver_url) == []


def test_partial_write_commits_but_reports_failure(urls, faults):
    toolserver_url, proxy_url = urls
    faults([{"type": "partial_write", "target": "place_order"}])
    assert _place(proxy_url).status_code == 500
    assert len(_orders(toolserver_url)) == 1


def test_partial_write_skips_rejected_writes(urls, faults):
    toolserver_url, proxy_url = urls
    faults([{"type": "partial_write", "target": "place_order"}])
    r = _place(proxy_url, qty=10_000)
    assert r.status_code == 409
    assert "x-faultline-injected" not in r.headers


def test_semantic_faults_rewrite_values(urls, faults):
    toolserver_url, proxy_url = urls
    true_qty = httpx.get(f"{toolserver_url}/tools/search_inventory", params={"sku": "A-22"}).json()["qty"]
    faults([
        {"type": "stale_data", "target": "search_inventory", "params": {"qty_drift": 0.5}},
        {"type": "plausible_wrong", "target": "get_pricing", "params": {"factor": 0.4}},
    ])
    stale = httpx.get(f"{proxy_url}/tools/search_inventory", params={"sku": "A-22"}).json()
    assert stale["qty"] == int(true_qty * 1.5) + 20
    price = httpx.get(f"{proxy_url}/tools/get_pricing", params={"sku": "D-40", "tier": "standard"}).json()
    assert price["unit_price"] == round(33.75 * 0.6, 2)


def test_malformed_json(urls, faults):
    _, proxy_url = urls
    faults([{"type": "malformed_json", "target": "search_inventory"}])
    r = httpx.get(f"{proxy_url}/tools/search_inventory", params={"sku": "A-22"})
    assert r.status_code == 200
    with pytest.raises(json.JSONDecodeError):
        json.loads(r.text)


def test_max_fires_and_target_lists(urls, faults):
    _, proxy_url = urls
    faults([{"type": "rate_limit", "target": ["get_customer", "get_pricing"], "max_fires": 1}])
    assert httpx.get(f"{proxy_url}/tools/search_inventory", params={"sku": "A-22"}).status_code == 200
    assert httpx.get(f"{proxy_url}/tools/get_customer", params={"id": "CUST-100"}).status_code == 429
    assert httpx.get(f"{proxy_url}/tools/get_customer", params={"id": "CUST-100"}).status_code == 200
    log = httpx.get(f"{proxy_url}/_proxy/injections").json()
    assert log["injections"] == [{"tool": "get_customer", "fault": "rate_limit", "phase": "request"}]


def test_admin_paths_are_never_faulted(urls, faults):
    _, proxy_url = urls
    faults([{"type": "rate_limit", "target": "*"}])
    assert httpx.get(f"{proxy_url}/_admin/state").status_code == 200


def test_firing_is_reproducible_per_seed(urls, faults):
    _, proxy_url = urls

    def pattern(seed):
        faults([{"type": "rate_limit", "rate": 0.5, "params": {"retry_after": 0}}], seed=seed)
        return [
            httpx.get(f"{proxy_url}/tools/get_customer", params={"id": "CUST-100"}).status_code
            for _ in range(12)
        ]

    assert pattern(3) == pattern(3)
    assert 429 in pattern(3) and 200 in pattern(3)


def test_config_errors(urls):
    _, proxy_url = urls
    assert httpx.post(f"{proxy_url}/_proxy/config", params={"name": "nope"}).status_code == 404
    bad = httpx.post(f"{proxy_url}/_proxy/config", params={"name": "x"}, json={"faults": [{"type": "nope"}]})
    assert bad.status_code == 400


def test_status_reports_upstream(urls):
    toolserver_url, proxy_url = urls
    assert httpx.get(f"{proxy_url}/_proxy/status").json()["upstream"] == toolserver_url
