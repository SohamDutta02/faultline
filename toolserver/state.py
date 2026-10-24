import random
from copy import deepcopy
from datetime import timedelta

from . import fixtures


class NotFound(Exception):
    pass


class Rejected(Exception):
    pass


class World:
    def __init__(self):
        self.reset(0)

    def reset(self, seed: int = 0):
        rng = random.Random(seed)
        self.seed = seed
        self.tick = 0
        self.order_counter = 0
        self.orders = {}
        # request_id -> the first response to that write, for idempotent retries
        self.requests = {}

        self.inventory = {}
        for row in deepcopy(fixtures.SKUS):
            spread = int(row["qty"] * fixtures.QTY_JITTER)
            row["qty"] = max(0, row["qty"] + rng.randint(-spread, spread))
            row["updated_at"] = self._now()
            self.inventory[row["sku"]] = row

        self.customers = {c["id"]: c for c in deepcopy(fixtures.CUSTOMERS)}

    def _now(self):
        return fixtures.BASE_TIME + timedelta(seconds=self.tick)

    def _advance(self):
        self.tick += 1

    def search_inventory(self, sku: str):
        row = self.inventory.get(sku)
        if row is None:
            raise NotFound(f"unknown sku {sku}")
        return {
            "sku": row["sku"],
            "name": row["name"],
            "qty": row["qty"],
            "updated_at": row["updated_at"].isoformat(),
        }

    def get_customer(self, customer_id: str):
        c = self.customers.get(customer_id)
        if c is None:
            raise NotFound(f"unknown customer {customer_id}")
        return dict(c)

    def get_pricing(self, sku: str, tier: str):
        row = self.inventory.get(sku)
        if row is None:
            raise NotFound(f"unknown sku {sku}")
        mult = fixtures.TIER_MULTIPLIER.get(tier)
        if mult is None:
            raise NotFound(f"unknown tier {tier}")
        return {"sku": sku, "tier": tier, "unit_price": round(row["base_price"] * mult, 2)}

    def list_orders(self, customer_id: str):
        if customer_id not in self.customers:
            raise NotFound(f"unknown customer {customer_id}")
        return [o for o in self.orders.values() if o["customer_id"] == customer_id]

    def _replay(self, request_id: str | None, tool: str, args: dict):
        """The stored response if this write was already done under request_id."""
        if not request_id or request_id not in self.requests:
            return None
        seen = self.requests[request_id]
        if (seen["tool"], seen["args"]) != (tool, args):
            raise Rejected(f"request_id {request_id} was already used for a different request")
        return {**seen["result"], "replayed": True}

    def _remember(self, request_id: str | None, tool: str, args: dict, result: dict) -> dict:
        if request_id:
            self.requests[request_id] = {"tool": tool, "args": args, "result": dict(result)}
        return result

    def place_order(self, customer_id: str, sku: str, qty: int, request_id: str | None = None):
        args = {"customer_id": customer_id, "sku": sku, "qty": qty}
        replay = self._replay(request_id, "place_order", args)
        if replay is not None:
            return replay

        customer = self.get_customer(customer_id)
        row = self.inventory.get(sku)
        if row is None:
            raise NotFound(f"unknown sku {sku}")
        if qty <= 0:
            raise Rejected("qty must be positive")
        if qty > row["qty"]:
            raise Rejected(f"insufficient stock: requested {qty}, available {row['qty']}")

        price = self.get_pricing(sku, customer["tier"])["unit_price"]
        total = round(price * qty, 2)
        committed = sum(
            o["total"] for o in self.orders.values()
            if o["customer_id"] == customer_id and o["status"] == "placed"
        )
        if committed + total > customer["credit_limit"]:
            raise Rejected("credit limit exceeded")

        self._advance()
        self.order_counter += 1
        order_id = f"ORD-{self.order_counter:04d}"
        row["qty"] -= qty
        row["updated_at"] = self._now()
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "sku": sku,
            "qty": qty,
            "unit_price": price,
            "total": total,
            "status": "placed",
            "created_at": self._now().isoformat(),
        }
        self.orders[order_id] = order
        return self._remember(request_id, "place_order", args, dict(order))

    def cancel_order(self, order_id: str, request_id: str | None = None):
        replay = self._replay(request_id, "cancel_order", {"order_id": order_id})
        if replay is not None:
            return replay

        order = self.orders.get(order_id)
        if order is None:
            raise NotFound(f"unknown order {order_id}")
        if order["status"] == "cancelled":
            raise Rejected("already cancelled")

        self._advance()
        order["status"] = "cancelled"
        row = self.inventory[order["sku"]]
        row["qty"] += order["qty"]
        row["updated_at"] = self._now()
        return self._remember(request_id, "cancel_order", {"order_id": order_id}, {"ok": True, "order_id": order_id})

    def snapshot(self):
        return {
            "seed": self.seed,
            "tick": self.tick,
            "inventory": {
                k: {**v, "updated_at": v["updated_at"].isoformat()}
                for k, v in self.inventory.items()
            },
            "customers": self.customers,
            "orders": self.orders,
        }

world = World()