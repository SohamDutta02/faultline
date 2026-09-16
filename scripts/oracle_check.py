import httpx

from tasks.catalog import BY_ID
from tasks.verify import verify

BASE = "http://127.0.0.1:8100"


def snapshot(c):
    return c.get(f"{BASE}/_admin/state").json()


def run(task_id, seed, solve):
    with httpx.Client(timeout=10) as c:
        c.post(f"{BASE}/_admin/reset", params={"seed": seed})
        before = snapshot(c)
        answer = solve(c, before)
        after = snapshot(c)
    v = verify(BY_ID[task_id], before, after, answer)
    print(f"{task_id}: {v.outcome.value} {v.reason}")


def solve_t1(c, before):
    r = c.get(f"{BASE}/tools/search_inventory", params={"sku": "A-22"})
    return r.json()["qty"]


def solve_t2(c, before):
    c.get(f"{BASE}/tools/search_inventory", params={"sku": "A-22"})
    c.post(f"{BASE}/tools/place_order", params={"customer_id": "CUST-100", "sku": "A-22", "qty": 5})
    return "done"


def solve_t3(c, before):
    cust = c.get(f"{BASE}/tools/get_customer", params={"id": "CUST-102"}).json()
    price = c.get(f"{BASE}/tools/get_pricing", params={"sku": "B-17", "tier": cust["tier"]}).json()
    stock = c.get(f"{BASE}/tools/search_inventory", params={"sku": "B-17"}).json()
    qty = min(int(cust["credit_limit"] // price["unit_price"]), stock["qty"])
    c.post(f"{BASE}/tools/place_order", params={"customer_id": "CUST-102", "sku": "B-17", "qty": qty})
    return f"ordered {qty}"


if __name__ == "__main__":
    run("t1_stock_lookup", 42, solve_t1)
    run("t2_simple_order", 42, solve_t2)
    run("t3_budget_order", 42, solve_t3)