import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field

import httpx

import settings

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
READ_ONLY = [s["name"] for s in SPECS if s["method"] == "GET"]
WRITES = [s["name"] for s in SPECS if s["method"] != "GET"]

# what the model is told about writes when the environment makes them idempotent
IDEMPOTENT_NOTES = {
    "place_order": " Idempotent: repeating exactly the same call in this session returns the original order "
    "instead of placing a second one.",
    "cancel_order": " Idempotent: repeating exactly the same call in this session returns the original result.",
}


def llm_tools(names: list[str] | None = None) -> list[dict]:
    return [
        {"name": s["name"], "description": s["description"], "input_schema": s["input_schema"]}
        for s in SPECS
        if names is None or s["name"] in names
    ]


@dataclass
class Event:
    step_idx: int
    kind: str
    tool_name: str | None = None
    fault_injected: bool = False
    latency_ms: int = 0
    payload: dict = field(default_factory=dict)


@dataclass
class Response:
    body: str
    status: int | None
    fault: str | None = None
    retry_after: float | None = None

    @property
    def is_error(self) -> bool:
        return self.status is None or self.status >= 400


def _seconds(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:  # HTTP-date form; fall back to normal backoff
        return None


class Toolbox:
    """Executes the agent's tool calls over HTTP and records an event log.

    With idempotent=True every write carries a request_id derived from this
    toolbox's nonce, the tool and its arguments, so the toolserver performs the
    same write at most once per trial however often it is sent."""

    def __init__(self, base_url: str | None = None, timeout: float = 20.0, on_event=None, idempotent: bool = False):
        self.base_url = base_url or settings.PROXY_URL
        self.idempotent = idempotent
        self.nonce = uuid.uuid4().hex[:8]
        self.client = httpx.Client(timeout=timeout)
        self.events: list[Event] = []
        self.on_event = on_event
        self.call_count = 0
        self.error_count = 0
        self.retries = 0

    def log(self, kind: str, **kw):
        event = Event(step_idx=len(self.events), kind=kind, **kw)
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def llm_tools(self, names: list[str] | None = None) -> list[dict]:
        tools = llm_tools(names)
        if self.idempotent:
            for t in tools:
                t["description"] += IDEMPOTENT_NOTES.get(t["name"], "")
        return tools

    def request_id(self, name: str, args: dict) -> str:
        blob = json.dumps([self.nonce, name, args], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def _params(self, spec: dict, args: dict) -> dict:
        if self.idempotent and spec["method"] != "GET":
            return {**args, "request_id": self.request_id(spec["name"], args)}
        return args

    def _request(self, spec: dict, args: dict) -> Response:
        url = self.base_url + spec["path"]
        params = self._params(spec, args)
        try:
            if spec["method"] == "GET":
                r = self.client.get(url, params=params)
            else:
                r = self.client.post(url, params=params)
        except Exception as exc:
            return Response(json.dumps({"error": "transport", "detail": type(exc).__name__}), None)
        return Response(
            r.text,
            r.status_code,
            fault=r.headers.get("x-faultline-injected"),
            retry_after=_seconds(r.headers.get("retry-after")),
        )

    def _attempt(self, name: str, spec: dict, args: dict) -> Response:
        start = time.perf_counter()
        resp = self._request(spec, args)
        payload = {"body": resp.body[:2000], "status": resp.status, "error": resp.is_error, "fault": resp.fault}
        if self.idempotent and spec["method"] != "GET":
            payload["request_id"] = self.request_id(name, args)
        self.log(
            "tool_result",
            tool_name=name,
            fault_injected=resp.fault is not None,
            latency_ms=int((time.perf_counter() - start) * 1000),
            payload=payload,
        )
        return resp

    def execute(self, name: str, args: dict) -> Response:
        return self._attempt(name, BY_NAME[name], args)

    def call(self, name: str, args: dict, allowed: list[str] | None = None) -> tuple[str, bool]:
        if name not in BY_NAME or (allowed is not None and name not in allowed):
            self.error_count += 1
            return json.dumps({"error": "unknown_tool", "detail": name}), True

        self.call_count += 1
        self.log("tool_call", tool_name=name, payload=args)
        resp = self.execute(name, args)
        if resp.is_error:
            self.error_count += 1
        return resp.body, resp.is_error

    def close(self):
        self.client.close()


def _parses(body: str) -> bool:
    try:
        json.loads(body)
        return True
    except ValueError:
        return False


class ResilientToolbox(Toolbox):
    """Toolbox with client-side retries, the way a production SDK would add them.

    Reads are idempotent, so they are retried on any transport-level failure or
    unparseable body. Writes are retried only on 429, which guarantees the
    request was not executed; a 5xx on a write may have committed, so it is
    passed through to the agent rather than risk a duplicate. When the
    environment makes writes idempotent, a repeat can't act twice, so writes
    are retried like reads.
    """

    max_attempts = 3
    max_backoff_s = 2.0
    base_backoff_s = 0.25

    def _retryable(self, spec: dict, resp: Response) -> bool:
        if resp.status == 429:
            return True
        if spec["method"] != "GET" and not self.idempotent:
            return False
        if resp.status is None or resp.status in (500, 502, 503, 504):
            return True
        return resp.status < 400 and not _parses(resp.body)

    def execute(self, name: str, args: dict) -> Response:
        spec = BY_NAME[name]
        for attempt in range(1, self.max_attempts + 1):
            resp = self._attempt(name, spec, args)
            if attempt == self.max_attempts or not self._retryable(spec, resp):
                return resp
            delay = resp.retry_after if resp.retry_after is not None else self.base_backoff_s * 2 ** (attempt - 1)
            delay = min(delay, self.max_backoff_s)
            self.retries += 1
            self.log("retry", tool_name=name, payload={"attempt": attempt, "status": resp.status, "delay_s": delay})
            time.sleep(delay)
        return resp
