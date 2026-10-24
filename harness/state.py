"""State: what the harness keeps track of between the model's steps.

`transcript` leaves it to the model to remember what happened. `ledger` keeps
a record the model can't lose track of: every write and how it ended (done,
rejected, not executed, or unknown until list_orders settles it), writes that
went through more than once, reads that contradict each other, and reads
that came back unreadable. The ledger is rebuilt from the agent's own tool
calls and responses; it never sees which faults the proxy injected.
"""

import json
from dataclasses import dataclass, field

from agents.toolbox import WRITES

from .base import Option

HEADER = "Harness state (tracked by the harness from your tool calls; it is not a tool response):"

# reads whose result a write can legitimately change
_STATIC_READS = {"get_customer", "get_pricing"}


def _fmt(tool: str, args: dict) -> str:
    return f"{tool}(" + ", ".join(f"{k}={args[k]}" for k in sorted(args)) + ")"


def _parse(body: str):
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


@dataclass
class WriteRecord:
    step: int
    tool: str
    args: dict
    outcome: str  # done | rejected | not_executed | unknown | went_through | did_not_go_through
    detail: str = ""
    order_id: str | None = None
    known_before: set = field(default_factory=set)  # order ids already seen when the write was made

    @property
    def committed(self) -> bool:
        return self.outcome in ("done", "went_through")

    def line(self) -> str:
        call = _fmt(self.tool, self.args)
        if self.outcome == "done":
            return (f"{call}: done" + (f" ({self.order_id})" if self.order_id else "")
                    + (f"; {self.detail}" if self.detail else ""))
        if self.outcome == "went_through":
            return f"{call}: reported an error but went through" + (f" as {self.order_id}" if self.order_id else "") + \
                f" ({self.detail}). Do not repeat it."
        if self.outcome == "did_not_go_through":
            return f"{call}: reported an error and did not go through ({self.detail})."
        if self.outcome == "not_executed":
            return f"{call}: not executed ({self.detail}); safe to repeat."
        if self.outcome == "rejected":
            return f"{call}: rejected ({self.detail})."
        return f"{call}: UNKNOWN. It {self.detail} and may have gone through; call list_orders before repeating it."


class Ledger:
    def __init__(self):
        self.writes: list[WriteRecord] = []
        self.reads: dict[str, dict] = {}
        self.conflicts: list[str] = []
        self.unreadable: dict[str, str] = {}
        self.known_orders: dict[str, dict] = {}
        self.step = 0

    # ---- observing -----------------------------------------------------------

    def observe(self, tool: str, args: dict, body: str, status: int | None):
        self.step += 1
        payload = _parse(body)
        if tool in WRITES:
            self._write(tool, args, payload, status)
        else:
            self._read(tool, args, payload, status)

    def _write(self, tool, args, payload, status):
        rec = WriteRecord(self.step, tool, dict(args), "unknown", known_before=set(self.known_orders))
        ok = status is not None and 200 <= status < 300
        if ok and isinstance(payload, dict):
            rec.outcome = "done"
            rec.order_id = payload.get("order_id")
            if payload.get("replayed"):
                rec.detail = "replayed: the original write had already gone through"
        elif ok:
            rec.detail = f"returned {status} with an unreadable body"
        elif status == 429:
            rec.outcome, rec.detail = "not_executed", "rate limited before it ran"
        elif status is not None and 400 <= status < 500:
            reason = payload.get("detail") if isinstance(payload, dict) else None
            rec.outcome, rec.detail = "rejected", reason or f"status {status}"
        else:
            rec.detail = f"returned {status}" if status else "failed in transport"
        self.writes.append(rec)

    def _read(self, tool, args, payload, status):
        key = _fmt(tool, args)
        if status is None or not (200 <= status < 300):
            return  # a failed read says nothing about the world
        if not isinstance(payload, (dict, list)):
            self.unreadable[key] = f"{key}: the last response was unreadable; re-read it before relying on it."
            return
        self.unreadable.pop(key, None)

        if tool == "list_orders" and isinstance(payload, dict):
            self._reconcile(args.get("customer_id"), payload.get("orders") or [])

        value = {k: v for k, v in payload.items() if k != "updated_at"} if isinstance(payload, dict) else payload
        seen = self.reads.get(key)
        if seen is not None and seen["value"] != value and not self._explained(tool, args, seen["step"]):
            note = (f"{key} gave different results with no write in between that explains it: "
                    f"{json.dumps(seen['value'], sort_keys=True)} then {json.dumps(value, sort_keys=True)}. "
                    "At least one of them is wrong.")
            if note not in self.conflicts:
                self.conflicts.append(note)
        self.reads[key] = {"value": value, "step": self.step}

    def _explained(self, tool: str, args: dict, since: int) -> bool:
        """Could one of the agent's own writes since step `since` have changed this read?"""
        if tool in _STATIC_READS:
            return False
        for w in self.writes:
            if w.step <= since or w.outcome in ("rejected", "not_executed", "did_not_go_through"):
                continue
            if tool != "search_inventory" or w.tool == "cancel_order" or w.args.get("sku") == args.get("sku"):
                return True
        return False

    def _reconcile(self, customer_id: str | None, orders: list[dict]):
        by_id = {o.get("order_id"): o for o in orders if isinstance(o, dict)}
        claimed = {w.order_id for w in self.writes if w.committed and w.order_id}
        for w in self.writes:
            if w.outcome != "unknown":
                continue
            if w.tool == "place_order" and w.args.get("customer_id") == customer_id:
                match = [
                    oid for oid, o in by_id.items()
                    if o.get("status") == "placed" and o.get("sku") == w.args.get("sku")
                    and o.get("qty") == w.args.get("qty") and oid not in claimed and oid not in w.known_before
                ]
                if match:
                    w.outcome, w.order_id, w.detail = "went_through", match[0], "seen in list_orders"
                    claimed.add(match[0])
                else:
                    w.outcome, w.detail = "did_not_go_through", "no matching order in list_orders"
            elif w.tool == "cancel_order" and w.args.get("order_id") in by_id:
                status = by_id[w.args["order_id"]].get("status")
                if status == "cancelled":
                    w.outcome, w.detail = "went_through", "list_orders shows it cancelled"
                elif status == "placed":
                    w.outcome, w.detail = "did_not_go_through", "list_orders shows it still placed"
        self.known_orders.update(by_id)

    # ---- rendering -----------------------------------------------------------

    def duplicates(self) -> list[str]:
        groups: dict[str, list[WriteRecord]] = {}
        for w in self.writes:
            if w.committed and not w.detail.startswith("replayed"):
                groups.setdefault(_fmt(w.tool, w.args), []).append(w)
        return [
            f"{call} has gone through {len(ws)} times"
            + (f" ({', '.join(w.order_id for w in ws if w.order_id)})" if any(w.order_id for w in ws) else "")
            + ". If it was meant to happen once, undo the extra ones."
            for call, ws in groups.items() if len(ws) > 1
        ]

    def render(self) -> str:
        warnings = self.duplicates() + self.conflicts + list(self.unreadable.values())
        if not self.writes and not warnings:
            return ""
        lines = [HEADER]
        if self.writes:
            lines += ["Writes so far:"] + [f"- {w.line()}" for w in self.writes]
        if warnings:
            lines += ["Warnings:"] + [f"- {x}" for x in warnings]
        return "\n".join(lines)


OPTIONS = {
    o.id: o
    for o in [
        Option("transcript", "the conversation history only; the model keeps track itself", {"kind": "transcript"},
               impl=None),
        Option(
            "ledger",
            "a harness-kept record of every write's outcome and of contradictory reads, shown to the model each turn",
            {"kind": "ledger", "shown": "appended to the system prompt before every model call", "header": HEADER},
            impl=Ledger,
            code=(WriteRecord, Ledger),
        ),
    ]
}


def make_state(option_id: str):
    cls = OPTIONS[option_id].impl
    return cls() if cls is not None else None
