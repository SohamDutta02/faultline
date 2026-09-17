from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .faults import REGISTRY, Fault

CONFIG_DIR = Path(__file__).parent / "configs"


@dataclass
class FaultConfig:
    name: str
    seed: int = 0
    faults: list[Fault] = field(default_factory=list)


def load(name: str) -> FaultConfig:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no fault config {name} at {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    faults = []
    for entry in raw.get("faults", []):
        cls = REGISTRY.get(entry["type"])
        if cls is None:
            raise ValueError(f"unknown fault type {entry['type']}")
        faults.append(
            cls(
                rate=float(entry.get("rate", 1.0)),
                target=entry.get("target", "*"),
                **entry.get("params", {}),
            )
        )
    return FaultConfig(name=name, seed=int(raw.get("seed", 0)), faults=faults)