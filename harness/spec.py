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

from .base import SLOTS, Option
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


def manifest(spec: HarnessSpec, slots: tuple[str, ...] = SLOTS, policy: dict | None = None) -> dict:
    """Everything that defines the harness, minus the model: what the fingerprint hashes."""
    out = {"policy": policy or {"id": "llm"}}
    for slot in slots:
        opt = OPTIONS[slot][getattr(spec, slot)]
        out[slot] = {"id": opt.id, "version": opt.version, "content": opt.content}
    return out


def fingerprint(manifest: dict) -> str:
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]
