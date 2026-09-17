import json
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class Injection:
    fired: bool
    fault_id: str | None = None
    status_code: int | None = None
    body: str | None = None
    headers: dict | None = None


class Fault:
    id = "base"
    layer = "transport"

    def __init__(self, rate: float = 1.0, target: str = "*", **params):
        self.rate = rate
        self.target = target
        self.params = params

    def matches(self, tool_name: str) -> bool:
        if self.target == "*":
            return True
        if self.target.endswith("*"):
            return tool_name.startswith(self.target[:-1])
        return tool_name == self.target

    def should_fire(self, rng: random.Random) -> bool:
        return rng.random() < self.rate

    def apply(self, tool_name: str, upstream: httpx.Response, rng: random.Random) -> Injection:
        raise NotImplementedError


class RateLimit(Fault):
    id = "rate_limit"
    layer = "transport"

    def apply(self, tool_name, upstream, rng):
        return Injection(
            True,
            self.id,
            429,
            json.dumps({"error": "rate_limited", "detail": "too many requests"}),
            {"retry-after": str(self.params.get("retry_after", 2))},
        )


class Timeout(Fault):
    id = "timeout"
    layer = "transport"

    def apply(self, tool_name, upstream, rng):
        time.sleep(self.params.get("delay_s", 30))
        return Injection(True, self.id, 504, json.dumps({"error": "gateway_timeout"}), {})


class MalformedJson(Fault):
    id = "malformed_json"
    layer = "schema"

    def apply(self, tool_name, upstream, rng):
        text = upstream.text
        mode = self.params.get("mode", "truncate")
        if mode == "truncate":
            body = text[: max(1, len(text) // 2)]
        elif mode == "unquoted":
            body = text.replace('"', "", 2)
        else:
            body = text + "}}"
        return Injection(True, self.id, 200, body, {})


class StaleData(Fault):
    id = "stale_data"
    layer = "semantic"

    def apply(self, tool_name, upstream, rng):
        try:
            data = upstream.json()
        except Exception:
            return Injection(False)
        if not isinstance(data, dict):
            return Injection(False)

        drift = self.params.get("qty_drift", 0.5)
        if "qty" in data and isinstance(data["qty"], int):
            data["qty"] = int(data["qty"] * (1 + drift)) + 20
        return Injection(True, self.id, 200, json.dumps(data), {})


class PlausibleWrong(Fault):
    id = "plausible_wrong"
    layer = "semantic"

    def apply(self, tool_name, upstream, rng):
        try:
            data = upstream.json()
        except Exception:
            return Injection(False)
        if not isinstance(data, dict):
            return Injection(False)

        for key in ("unit_price", "credit_limit", "qty"):
            if key in data and isinstance(data[key], (int, float)):
                factor = self.params.get("factor", 0.4)
                val = data[key] * (1 - factor)
                data[key] = int(val) if isinstance(data[key], int) else round(val, 2)
                return Injection(True, self.id, 200, json.dumps(data), {})
        return Injection(False)


class PartialWrite(Fault):
    id = "partial_write"
    layer = "semantic"

    def apply(self, tool_name, upstream, rng):
        if upstream.status_code >= 400:
            return Injection(False)
        return Injection(
            True,
            self.id,
            500,
            json.dumps({"error": "internal", "detail": "write may not have completed"}),
            {},
        )


REGISTRY = {
    c.id: c
    for c in [RateLimit, Timeout, MalformedJson, StaleData, PlausibleWrong, PartialWrite]
}