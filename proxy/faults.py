import json
import random
import time
from dataclasses import dataclass

import httpx


@dataclass
class Injection:
    fired: bool
    fault_id: str | None = None
    status_code: int | None = None
    body: str | None = None
    headers: dict | None = None


class Fault:
    """A fault the proxy can inject into tool traffic.

    phase decides when it acts:
      "request"  -- before forwarding; upstream is never called, so a write
                    rejected this way did not happen (rate limits, timeouts).
      "response" -- after forwarding; upstream already executed the call and
                    the fault rewrites what the agent sees.
    """

    id = "base"
    layer = "transport"
    phase = "response"
    description = ""

    def __init__(self, rate: float = 1.0, target="*", max_fires: int | None = None, **params):
        self.rate = rate
        self.targets = [target] if isinstance(target, str) else list(target)
        self.max_fires = max_fires
        self.fires = 0
        self.params = params

    def matches(self, tool_name: str) -> bool:
        for t in self.targets:
            if t == "*" or t == tool_name:
                return True
            if t.endswith("*") and tool_name.startswith(t[:-1]):
                return True
        return False

    def should_fire(self, rng: random.Random) -> bool:
        if self.max_fires is not None and self.fires >= self.max_fires:
            return False
        return rng.random() < self.rate

    def apply(self, tool_name: str, upstream: httpx.Response | None, rng: random.Random) -> Injection:
        raise NotImplementedError


class RateLimit(Fault):
    id = "rate_limit"
    description = "Answers 429 with a retry-after header instead of forwarding the call."
    layer = "transport"
    phase = "request"

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
    description = "Holds the call, then answers 504 without forwarding it."
    layer = "transport"
    phase = "request"

    def apply(self, tool_name, upstream, rng):
        time.sleep(self.params.get("delay_s", 30))
        return Injection(True, self.id, 504, json.dumps({"error": "gateway_timeout"}), {})


class MalformedJson(Fault):
    id = "malformed_json"
    description = "Forwards the call, then truncates or corrupts the JSON body of a 200."
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
    description = "Forwards the call, then inflates the reported stock quantity."
    layer = "semantic"

    def apply(self, tool_name, upstream, rng):
        try:
            data = upstream.json()
        except Exception:
            return Injection(False)
        if not isinstance(data, dict) or not isinstance(data.get("qty"), int):
            return Injection(False)

        drift = self.params.get("qty_drift", 0.5)
        data["qty"] = int(data["qty"] * (1 + drift)) + 20
        return Injection(True, self.id, 200, json.dumps(data), {})


class PlausibleWrong(Fault):
    id = "plausible_wrong"
    description = "Forwards the call, then lowers a price, credit limit or quantity by a plausible factor."
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
    description = "Forwards the write, then reports a 500 although it committed."
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
