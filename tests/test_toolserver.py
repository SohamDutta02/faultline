import httpx
import pytest

from toolserver.state import NotFound, Rejected, World


def test_reset_is_deterministic_per_seed():
    a, b = World(), World()
    a.reset(7)
    b.reset(7)
    assert a.snapshot()["inventory"] == b.snapshot()["inventory"]


def test_order_prices_by_tier_and_decrements_stock():
    w = World()
    w.reset(42)
    before = w.inventory["A-22"]["qty"]
    order = w.place_order("CUST-100", "A-22", 5)
    assert order["order_id"] == "ORD-0001"
    assert order["unit_price"] == round(0.62 * 0.85, 2)
    assert w.inventory["A-22"]["qty"] == before - 5


def test_rejects_bad_orders():
    w = World()
    w.reset(42)
    with pytest.raises(Rejected, match="insufficient stock"):
        w.place_order("CUST-100", "E-08", 1)
    with pytest.raises(Rejected, match="positive"):
        w.place_order("CUST-100", "A-11", 0)
    with pytest.raises(Rejected, match="credit limit"):
        w.place_order("CUST-102", "D-40", 23)
    with pytest.raises(NotFound):
        w.place_order("CUST-999", "A-11", 1)


def test_cancel_restores_stock_once():
    w = World()
    w.reset(42)
    before = w.inventory["C-02"]["qty"]
    oid = w.place_order("CUST-101", "C-02", 4)["order_id"]
    w.cancel_order(oid)
    assert w.inventory["C-02"]["qty"] == before
    with pytest.raises(Rejected, match="already cancelled"):
        w.cancel_order(oid)


def test_http_error_mapping(urls):
    toolserver_url, _ = urls
    assert httpx.get(f"{toolserver_url}/tools/search_inventory", params={"sku": "ZZ"}).status_code == 404
    r = httpx.post(f"{toolserver_url}/tools/place_order", params={"customer_id": "CUST-100", "sku": "E-08", "qty": 1})
    assert r.status_code == 409
    assert r.json()["error"] == "rejected"
