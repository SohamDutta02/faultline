import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE = os.getenv("TOOLSERVER_URL", "http://127.0.0.1:8100")

SPECS = [
    {
        "name": "search_inventory",
        "description": "Look up current stock level for a SKU.",
        "method": "GET",
        "path": "/tools/search_inventory",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "name": "get_customer",
        "description": "Fetch a customer record including tier and credit limit.",
        "method": "GET",
        "path": "/tools/get_customer",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_pricing",
        "description": "Get the unit price for a SKU at a given customer tier.",
        "method": "GET",
        "path": "/tools/get_pricing",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}, "tier": {"type": "string"}},
            "required": ["sku", "tier"],
        },
    },
    {
        "name": "list_orders",
        "description": "List all orders for a customer.",
        "method": "GET",
        "path": "/tools/list_orders",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "place_order",
        "description": "Place an order. Fails if stock or credit is insufficient.",
        "method": "POST",
        "path": "/tools/place_order",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "sku": {"type": "string"},
                "qty": {"type": "integer"},
            },
            "required": ["customer_id", "sku", "qty"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel a previously placed order and restore stock.",
        "method": "POST",
        "path": "/tools/cancel_order",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
]

BY_NAME = {s["name"]: s for s in SPECS}


def llm_tools() -> list[dict]:
    return [
        {"name": s["name"], "description": s["description"], "input_schema": s["input_schema"]}
        for s in SPECS
    ]


@dataclass
class Event:
    step_idx: int
    kind: str
    tool_name: str | None = None
    fault_injected: bool = False
    latency_ms: int = 0
    payload: dict = field(default_factory=dict)


class Toolbox:
    def __init__(self, base_url: str = DEFAULT_BASE, timeout: float = 20.0):
        self.base_url = base_url
        self.client = httpx.Client(timeout=timeout)
        self.events: list[Event] = []
        self.call_count = 0

    def log(self, kind: str, **kw):
        self.events.append(Event(step_idx=len(self.events), kind=kind, **kw))

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        spec = BY_NAME.get(name)
        if spec is None:
            return f'{{"error":"unknown_tool","detail":"{name}"}}', True

        self.call_count += 1
        self.log("tool_call", tool_name=name, payload=args)
        url = self.base_url + spec["path"]
        start = time.perf_counter()
        try:
            if spec["method"] == "GET":
                r = self.client.get(url, params=args)
            else:
                r = self.client.post(url, params=args)
            body = r.text
            is_error = r.status_code >= 400
        except Exception as exc:
            body = f'{{"error":"transport","detail":"{type(exc).__name__}"}}'
            is_error = True

        latency = int((time.perf_counter() - start) * 1000)
        faulted = "x-faultline-injected" in getattr(locals().get("r", None), "headers", {})
        self.log(
            "tool_result",
            tool_name=name,
            fault_injected=faulted,
            latency_ms=latency,
            payload={"body": body[:2000], "error": is_error},
        )
        return body, is_error

    def close(self):
        self.client.close()