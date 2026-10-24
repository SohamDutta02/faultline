"""Harness specs, presets, names and fingerprints.

An architecture id names a harness: a preset, optionally edited slot by slot.

  single                         the baseline: every slot at its first option
  retry                          a preset
  single+tools=retrying          the same harness as retry, spelled out
  critic+instructions=fault_aware
  full-feedback                  full with feedback reset to its baseline

"+slot=option" sets a slot, "-slot" resets it to the baseline option.
"""

import hashlib
import json
import re
from dataclasses import dataclass, replace

from .base import LLM_POLICY_VERSION, SLOTS, Option, code_hash
from .components import OPTIONS


class BadHarness(ValueError):
    pass


@dataclass(frozen=True)
class HarnessSpec:
    instructions: str = "base"
    tools: str = "plain"
    environment: str = "bare"
    state: str = "transcript"
    feedback: str = "none"

    def edit(self, **slots) -> "HarnessSpec":
        return replace(self, **slots)

    def differs(self, other: "HarnessSpec") -> list[str]:
        return [s for s in SLOTS if getattr(self, s) != getattr(other, s)]

    def as_dict(self) -> dict:
        return {s: getattr(self, s) for s in SLOTS}

    def options(self) -> dict[str, Option]:
        return {s: OPTIONS[s][getattr(self, s)] for s in SLOTS}


BASELINE = HarnessSpec()


@dataclass(frozen=True)
class Preset:
    id: str
    spec: HarnessSpec
    description: str


PRESETS = {
    p.id: p
    for p in [
        Preset("single", BASELINE, "one tool-calling loop, no safety nets"),
        Preset("retry", BASELINE.edit(tools="retrying"),
               "single + client-side retries for transport and schema faults"),
        Preset("critic", BASELINE.edit(feedback="critic"), "single + read-only reviewer that can send the work back"),
        Preset("checks", BASELINE.edit(feedback="checks"), "single + deterministic verification, no second model"),
        Preset(
            "full",
            HarnessSpec(instructions="fault_aware", tools="retrying", environment="idempotent", state="ledger",
                        feedback="checks"),
            "every subsystem at its strongest deployable option: fault-aware prompt, retrying client, "
            "idempotent writes, ledger, checks",
        ),
    ]
}

_NAME = re.compile(r"^([a-z][a-z0-9_]*)((?:[+-][a-z_]+(?:=[a-z0-9_]+)?)*)$")
_EDIT = re.compile(r"([+-])([a-z_]+)(?:=([a-z0-9_]+))?")


def split(name: str) -> tuple[str, list[tuple[str, str, str | None]]]:
    """'full-state+tools=plain' -> ('full', [('-', 'state', None), ('+', 'tools', 'plain')])"""
    m = _NAME.match(name or "")
    if not m:
        raise BadHarness(f"can't read harness name {name!r}; expected preset[+slot=option][-slot]...")
    return m.group(1), [(sign, slot, value or None) for sign, slot, value in _EDIT.findall(m.group(2))]


def apply_edits(spec: HarnessSpec, edits: list, allowed: tuple[str, ...] = SLOTS) -> HarnessSpec:
    for sign, slot, value in edits:
        if slot not in SLOTS:
            raise BadHarness(f"unknown subsystem {slot!r}; have {', '.join(SLOTS)}")
        if slot not in allowed:
            raise BadHarness(f"{slot} can't be changed here; only {', '.join(allowed)}")
        if sign == "-":
            if value:
                raise BadHarness(f"'-{slot}' resets {slot} to its baseline and takes no value")
            value = getattr(BASELINE, slot)
        elif value is None:
            raise BadHarness(f"'+{slot}' needs an option, one of {', '.join(OPTIONS[slot])}")
        elif value not in OPTIONS[slot]:
            raise BadHarness(f"unknown {slot} option {value!r}; have {', '.join(OPTIONS[slot])}")
        spec = spec.edit(**{slot: value})
    return spec


def parse(name: str) -> HarnessSpec:
    head, edits = split(name)
    if head not in PRESETS:
        raise BadHarness(f"unknown harness preset {head!r}; have {', '.join(PRESETS)}")
    return apply_edits(PRESETS[head].spec, edits)


def name_of(spec: HarnessSpec, base: str = "single") -> str:
    """A preset's id if one matches, else the base preset plus the slots that differ."""
    for p in PRESETS.values():
        if p.spec == spec:
            return p.id
    return base + "".join(f"+{s}={getattr(spec, s)}" for s in spec.differs(PRESETS[base].spec))


def ablation(name: str, base: str = "single") -> list[str]:
    """Controlled-variable harnesses around `name`: the base, the base plus each
    subsystem `name` changes (add-one), `name` minus each of them (leave-one-out),
    and `name` itself. Every neighbour differs from another in one subsystem.
    Names that spell the same harness are kept once (the first spelling)."""
    head, edits = split(name)
    spec = apply_edits(PRESETS[head].spec, edits) if head in PRESETS else None
    if spec is None:
        raise BadHarness(f"unknown harness preset {head!r}")
    base_spec = parse(base)
    if base_spec != BASELINE:
        raise BadHarness("an ablation's base must be the baseline harness")
    changed = spec.differs(base_spec)
    if not changed:
        raise BadHarness(f"{name} is the baseline; nothing to ablate")
    names = [base] + [f"{base}+{s}={getattr(spec, s)}" for s in changed] + [f"{name}-{s}" for s in changed] + [name]
    return dedupe(names, parse)


def dedupe(names: list[str], spec_of) -> list[str]:
    """Drop names whose harness an earlier name already spells, except the last
    one: the harness being ablated keeps the name it was asked for."""
    seen, out = {}, []
    for i, n in enumerate(names):
        spec = spec_of(n)
        if spec in seen and i != len(names) - 1:
            continue
        if spec in seen:
            out.remove(seen[spec])
        seen[spec] = n
        out.append(n)
    return out


def manifest(spec: HarnessSpec, slots: tuple[str, ...] = SLOTS, policy: dict | None = None) -> dict:
    """Everything that defines the harness, minus the model: what the fingerprint hashes."""
    out = {"policy": policy or {"id": "llm", "version": LLM_POLICY_VERSION}}
    for slot in slots:
        opt = OPTIONS[slot][getattr(spec, slot)]
        out[slot] = {"id": opt.id, "version": opt.version, "content": opt.content}
    return out


def code_hashes(spec: HarnessSpec, slots: tuple[str, ...] = SLOTS) -> dict:
    """slot -> hash of the source of the option's implementation (None when it has no code)."""
    return {slot: code_hash(OPTIONS[slot][getattr(spec, slot)].code) for slot in slots}


def changed_parts(old: dict, new: dict) -> list[str]:
    """The parts of a harness (policy and slots) whose manifest entries differ."""
    return [k for k in ("policy", *SLOTS) if k in old or k in new if old.get(k) != new.get(k)]


def fingerprint(manifest: dict) -> str:
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]
