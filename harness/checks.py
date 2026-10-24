"""Deterministic verification of the agent's work: the harness's test suite.

No model involved. From the agent's own tool calls (never the proxy's fault
headers) and fresh reads through a feedback channel, it checks:

- writes that returned an error: did they go through after all, or not?
- duplicates: more matching orders than calls that reported success
- facts the agent read: does a re-check agree, after accounting for the
  agent's own orders and cancellations?
- that there is a final answer

The checks know what the tools mean (that an order lowers stock, that prices
don't change mid-trial), as verification commands know the code they test.
They never look at the task's grading checks.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from agents.toolbox import READ_ONLY, WRITES

# fields of a read worth re-checking, by tool
FACTS = {
    "search_inventory": ("qty",),
    "get_customer": ("tier", "credit_limit"),
    "get_pricing": ("unit_price",),
}

# an answer that reports trouble; used to decide whether a write that
# "failed" but went through needs the agent to correct its report
_TROUBLE = ("fail", "error", "could not", "couldn't", "unable", "not placed", "did not", "didn't", "gave up",
            "not cancel", "wasn't", "was not")


@dataclass(frozen=True)
class Problem:
    kind: str  # "write", "duplicate", "fact" or "answer"
    text: str


@dataclass
class Call:
    idx: int
    tool: str
    args: dict
    status: int | None = None
    body: str = ""

    @property
    def payload(self):
        try:
            return json.loads(self.body)
        except (TypeError, ValueError):
            return None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300 and isinstance(self.payload, dict)

    @property
    def failed(self) -> bool:
        return not self.ok


def _get(e, key, default=None):
    return e.get(key, default) if isinstance(e, dict) else getattr(e, key, default)


def agent_calls(events) -> list[Call]:
    """The agent's tool calls with their final responses (after any client retries),
    from a trial's event log: live Event objects or stored dicts."""
    calls: list[Call] = []
    current = None
    for e in events:
        if (_get(e, "channel") or "agent") != "agent":
            continue
        kind = _get(e, "kind")
        if kind == "tool_call":
            current = Call(len(calls), _get(e, "tool_name"), dict(_get(e, "payload") or {}))
            calls.append(current)
        elif kind == "tool_result" and current is not None:
            payload = _get(e, "payload") or {}
            current.status = payload.get("status")
            current.body = payload.get("body", "")
    return calls


def _fmt(tool: str, args: dict) -> str:
    return f"{tool}(" + ", ".join(f"{k}={args[k]}" for k in sorted(args)) + ")"


def _reports_trouble(answer: str | None) -> bool:
    text = (answer or "").lower()
    return any(w in text for w in _TROUBLE)


def run_checks(calls: list[Call], read: Callable[[str, dict], dict | None], answer: str | None) -> list[Problem]:
    """Problems found, as messages for the agent; [] means the work checks out.

    read(tool, args) returns a fresh parsed response through the feedback
    channel, or None if that read failed."""
    problems: list[Problem] = []
    writes = [c for c in calls if c.tool in WRITES]
    first_write = writes[0].idx if writes else len(calls)
    last_write = writes[-1].idx if writes else -1

    # orders that existed before the agent's first write, and who owns which order
    known: dict[str, dict] = {}
    owner: dict[str, str] = {}
    for c in calls:
        if c.tool == "list_orders" and c.ok:
            for o in c.payload.get("orders") or []:
                owner[o["order_id"]] = o["customer_id"]
                if c.idx < first_write:
                    known.setdefault(o["order_id"], dict(o))
        elif c.tool == "place_order" and c.ok and "order_id" in c.payload:
            owner[c.payload["order_id"]] = c.args.get("customer_id")

    customers = {w.args.get("customer_id") for w in writes if w.tool == "place_order"}
    customers |= {owner.get(w.args.get("order_id")) for w in writes if w.tool == "cancel_order"}
    current: dict[str, list[dict]] = {}
    for cust in sorted(c for c in customers if c):
        fresh = read("list_orders", {"customer_id": cust})
        if fresh is not None:
            current[cust] = fresh.get("orders") or []

    # 1. orders: errors that went through, writes that didn't, duplicates
    intents: dict[tuple, list[Call]] = {}
    for w in writes:
        if w.tool == "place_order":
            key = (w.args.get("customer_id"), w.args.get("sku"), w.args.get("qty"))
            intents.setdefault(key, []).append(w)
    for (cust, sku, qty), attempts in intents.items():
        if cust not in current:
            continue
        call = _fmt("place_order", {"customer_id": cust, "sku": sku, "qty": qty})
        succeeded = [a for a in attempts if a.ok and not a.payload.get("replayed")]
        errored = [a for a in attempts if a.failed]
        placed = [o["order_id"] for o in current[cust]
                  if o.get("status") == "placed" and o.get("sku") == sku and o.get("qty") == qty
                  and o["order_id"] not in known]
        if errored and len(placed) > len(succeeded):
            if len(placed) > 1:
                problems.append(Problem("duplicate",
                    f"{call}: {len(placed)} matching orders exist ({', '.join(placed)}) but only "
                    f"{len(succeeded)} of your calls reported success; calls that returned an error went through. "
                    "If you meant to place this order once, cancel the extra ones."))
            elif _reports_trouble(answer):
                problems.append(Problem("answer", f"{call}: it returned an error, but it went through as {placed[0]}. "
                                "The order exists; correct your answer and don't place it again."))
        elif errored and not succeeded and not placed:
            problems.append(Problem("write", f"{call}: it returned an error and no matching order exists, "
                                             "so it did not go through."))
        elif len(placed) > 1:
            problems.append(Problem("duplicate", f"{call}: {len(placed)} identical orders exist ({', '.join(placed)}). "
                                             "Check that this was intended."))

    cancels: dict[str, list[Call]] = {}
    for w in writes:
        if w.tool == "cancel_order":
            cancels.setdefault(w.args.get("order_id"), []).append(w)
    for order_id, attempts in cancels.items():
        cust = owner.get(order_id)
        order = next((o for o in current.get(cust, []) if o.get("order_id") == order_id), None)
        if order is None or any(a.ok for a in attempts):
            continue
        call = _fmt("cancel_order", {"order_id": order_id})
        if order.get("status") == "placed":
            problems.append(Problem("write", f"{call}: it returned an error and the order is still placed, "
                                             "so it did not go through."))
        elif order.get("status") == "cancelled" and _reports_trouble(answer):
            problems.append(Problem("answer", f"{call}: it returned an error, but the order is cancelled. "
                                             "Correct your answer."))

    # 2. facts: re-read what the agent read before its first write or after its last
    # one, and compare, allowing for the agent's own orders and cancellations
    seen: dict[str, Call] = {}
    for c in calls:
        if c.tool in FACTS and c.tool in READ_ONLY and c.ok and (c.idx < first_write or c.idx > last_write):
            seen[_fmt(c.tool, c.args)] = c
    session_placed = [o for orders in current.values() for o in orders
                      if o.get("status") == "placed" and o["order_id"] not in known]
    session_cancelled = [o for o in known.values() if o.get("status") == "placed" and any(
        x.get("order_id") == o["order_id"] and x.get("status") == "cancelled"
        for orders in current.values() for x in orders)]
    for key, c in seen.items():
        fresh = read(c.tool, c.args)
        if fresh is None:
            continue
        for f in FACTS[c.tool]:
            if f not in c.payload or f not in fresh:
                continue
            saw, now = c.payload[f], fresh[f]
            expected = saw
            if c.tool == "search_inventory" and c.idx < first_write and isinstance(saw, (int, float)):
                sku = c.args.get("sku")
                expected = (saw - sum(o["qty"] for o in session_placed if o.get("sku") == sku)
                            + sum(o["qty"] for o in session_cancelled if o.get("sku") == sku))
            same = abs(now - expected) < 0.005 if isinstance(now, (int, float)) and isinstance(expected, (int, float)) \
                else now == expected
            if not same:
                adjusted = f" (so {expected} now, after your own orders)" if expected != saw else ""
                problems.append(Problem("fact", f"{key}: you saw {f} {saw}{adjusted}; a re-check gives {now}. "
                                        "Work from the re-checked value."))

    if answer is None:
        problems.append(Problem("answer", "You did not give a final answer."))
    return problems


def reader(toolbox) -> Callable[[str, dict], dict | None]:
    """read(tool, args) through a tool client, None if the read failed."""

    def read(tool: str, args: dict) -> dict | None:
        body, is_error = toolbox.call(tool, args)
        if is_error:
            return None
        try:
            payload = json.loads(body)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    return read
